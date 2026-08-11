package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.domain.*;
import com.shuyiwa.fitness.backend.global.FrogException;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.Optional;

@Service
public class AccountService {
    private static final Log logger = LogFactory.getLog(AccountService.class);

    @Autowired
    AccountRepository accountRepository;

    @Autowired
    BillRepository billRepository;
    @Autowired
    LoginUserTaskProgressRepository loginUserTaskProgressRepository;

    @Transactional
    public Account findOrCreateAccount(Optional<LoginUser> loginUser, CurrencyType currencyType) {
        if (loginUser.isPresent()) {
            Optional<Account> optionalAccount = accountRepository.findByLoginUserAndCurrencyType(loginUser, currencyType);
//            Optional<Account> optionalAccount = accountRepository.findByLoginUser(loginUser);
            if (optionalAccount.isPresent()) {
                return optionalAccount.get();
            } else {
                accountRepository.insertIgnore(loginUser.get().getId(), currencyType.name());
                return accountRepository.findByLoginUserAndCurrencyType(loginUser, currencyType).get();
//                return accountRepository.findByLoginUser(loginUser).get();
            }
        } else {
            return null;
        }
    }

    @Transactional(rollbackFor = Throwable.class)
    public Account reward(String name, Optional<LoginUser> loginUser, BigDecimal value) {
        return reward(name, loginUser, value, "");
    }


    @Transactional(rollbackFor = Throwable.class)
    public Account subtract(String name, LoginUser loginUser, BigDecimal value, String memo) throws FrogException {
        Account account = findOrCreateAccount(Optional.ofNullable(loginUser), CurrencyType.point);
        BigDecimal newValue = account.getBalance().subtract(value);
        if (newValue.compareTo(BigDecimal.ZERO) < 0) {
            throw new FrogException(FrogException.BALANCE_NOT_ENOUGH, "积分不足");
        }
        account.setBalance(newValue);
        accountRepository.save(account);

        Bill bill = new Bill();
        bill.setValue(value);
        bill.setAccount(account);
        bill.setName(name);
        bill.setMemo(memo);
        billRepository.save(bill);
        return account;
    }

    @Transactional(rollbackFor = Throwable.class)
    public Account reward(String name, Optional<LoginUser> loginUser, BigDecimal value, LoginUserTaskProgress loginUserTaskProgress) {

        Account account = findOrCreateAccount(loginUser, CurrencyType.point);
        if (loginUserTaskProgress.getBill() == null) {
            account.setBalance(account.getBalance().add(value));
            accountRepository.save(account);

            Bill bill = new Bill();
            bill.setValue(value);
            bill.setAccount(account);
            bill.setName(name);
            bill.setMemo(loginUserTaskProgress.getLoginUserTask().getId());
            billRepository.save(bill);

            loginUserTaskProgress.setBill(bill);
            loginUserTaskProgressRepository.save(loginUserTaskProgress);
        }

        return account;
    }

    @Transactional(rollbackFor = Throwable.class)
    public Account reward(String name, Optional<LoginUser> loginUser, BigDecimal value, String memo) {

        Account account = findOrCreateAccount(loginUser, CurrencyType.point);
        account.setBalance(account.getBalance().add(value));
        accountRepository.save(account);

        Bill bill = new Bill();
        bill.setValue(value);
        bill.setAccount(account);
        bill.setName(name);
        bill.setMemo(memo);
        billRepository.save(bill);
        return account;
    }
}
