package com.shuyiwa.fitness.backend.domain;

import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.service.AccountService;
import com.shuyiwa.fitness.backend.service.LoginUserService;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import com.shuyiwa.fitness.backend.service.ItemService;
import org.junit.Assert;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.domain.PageRequest;
import org.springframework.orm.ObjectOptimisticLockingFailureException;
import org.springframework.test.context.junit4.SpringRunner;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Date;
import java.util.Optional;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;


@RunWith(SpringRunner.class)
@SpringBootTest
@ConditionalOnProperty(value = "spring.profiles.active", havingValue = "local")
public class ItemServiceOnMysqlTest {
    @Autowired
    ItemService itemService;
    @Autowired
    LoginUserService loginUserService;
    @Autowired
    ItemRepository itemRepository;
    @Autowired
    AccountService accountService;
    @Autowired
    BillRepository billRepository;
    @Autowired
    LoginUserRepository loginUserRepository;

    @Autowired
    ItemInstanceRepository instanceRepository;


    @Test
    public void testBuyWithoutConflict() throws FrogException {
        LoginUser loginUser = loginUserService.createLoginUser((int) (Math.random() * 100000000.) + "",null);
        accountService.reward("系统赠送", Optional.ofNullable(loginUser), new BigDecimal(100));
        Item item = createItem();
        FrogUserDetails frogUserDetails = new FrogUserDetails(loginUser, new ArrayList<>());
        try {
            itemService.buy(frogUserDetails, item.getId());
        } catch (Throwable e) {
            e.printStackTrace();
        }
        assertEquals(2, billRepository.findByAccount_loginUser(loginUser, PageRequest.of(0, 1000)).stream().count(), 2);
        Assert.assertEquals(new BigDecimal(99), accountService.findOrCreateAccount(Optional.ofNullable(loginUser), CurrencyType.point).getBalance());
        assertEquals(1, instanceRepository.myValidateItem(Item.ItemType.SIMPLE.name(), loginUser.getId(), PageRequest.of(0, 1)).getTotalElements());
    }

    @Test
    public void testBuyWithConflict() throws FrogException, InterruptedException {

        LoginUser loginUser = loginUserService.createLoginUser((int) (Math.random() * 100000000.) + "",null);
        int balanceBefore = 700;
        int price = 3;
        accountService.reward("系统赠送", Optional.ofNullable(loginUser), new BigDecimal(balanceBefore));
        int socks = 200;
        Item item = createSimpleItem(socks, price);
        ExecutorService pool = Executors.newFixedThreadPool(100);
        for (int i = 0; i < 200; i++) {
            pool.submit(() -> {
                FrogUserDetails frogUserDetails = new FrogUserDetails(loginUserRepository.findById(loginUser.getId()).orElse(null), new ArrayList<>());
                for (int j = 0; j < 30; j++) {
                    try {
                        itemService.buy(frogUserDetails, item.getId());
                        break;
                    } catch (FrogException e) {
                        e.printStackTrace();
                    } catch (ObjectOptimisticLockingFailureException e) {
                        System.err.println("冲突，购买失败");
                    } catch (Throwable e) {
                        System.err.println("冲突，购买失败");
                    }
                }
            });
        }
        pool.shutdown();
        pool.awaitTermination(10, TimeUnit.MINUTES);
        long itemBillCount = billRepository.findByAccount_loginUser(loginUser, PageRequest.of(0, 1000)).stream().count() - 1;//其中一个是账户充值的，剩下的是购买生成的账单
        BigDecimal balance = accountService.findOrCreateAccount(Optional.ofNullable(loginUser), CurrencyType.point).getBalance();
        long myItemCount = instanceRepository.myValidateItem(Item.ItemType.SIMPLE.name(), loginUser.getId(), PageRequest.of(0, 10)).getTotalElements();
        Item newItem = itemRepository.findById(item.getId()).get();

        assertTrue("有购买成功的，所以itemBillCount应该大于0", itemBillCount > 0);
        assertTrue("帐户余额应该等于balanceBefore - 订单数*订单单价", balance.compareTo(new BigDecimal(balanceBefore).subtract(new BigDecimal(itemBillCount).multiply(new BigDecimal(price)))) == 0);
        assertTrue("我购买的互动卡数应该等于订单数", myItemCount == itemBillCount);
        assertTrue("库存应该等于原库存-我的互动卡数", newItem.getStocks() + myItemCount == socks);
    }

    @Transactional
    private Item createItem() {
        Item item = new Item();
        item.setItemType(Item.ItemType.SIMPLE);
        item.setStocks(1L);
        item.setValidateStartTime(new Date(System.currentTimeMillis() - Duration.ofDays(1).toMillis()));
        item.setValidateEndTime(new Date(System.currentTimeMillis() + Duration.ofDays(1).toMillis()));
        item.setPrice(new BigDecimal(1));
        itemRepository.save(item);
        return item;
    }

    @Transactional
    private Item createSimpleItem(long socks, int price) {
        Item item = new Item();
        item.setItemType(Item.ItemType.SIMPLE);
        item.setStocks(socks);
        item.setValidateStartTime(new Date(System.currentTimeMillis() - Duration.ofDays(1).toMillis()));
        item.setValidateEndTime(new Date(System.currentTimeMillis() + Duration.ofDays(1).toMillis()));
        item.setPrice(new BigDecimal(price));
        itemRepository.save(item);
        return item;
    }
}
