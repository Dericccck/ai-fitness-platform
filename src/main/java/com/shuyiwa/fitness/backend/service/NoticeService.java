package com.shuyiwa.fitness.backend.service;

import com.shuyiwa.fitness.backend.domain.MenuService;
import com.shuyiwa.fitness.backend.domain.Notice;
import com.shuyiwa.fitness.backend.domain.NoticeContainRepository;
import com.shuyiwa.fitness.backend.global.FrogException;
import com.shuyiwa.fitness.backend.sec.FrogUserDetailsService;
import com.shuyiwa.fitness.backend.sec.FrogUserDetails;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;
import java.util.stream.Collectors;

@Service
public class NoticeService {
    @Autowired
    NoticeContainRepository noticeContainRepository;
    @Autowired
    MenuService menuService;

    public void delete(String id) {
        noticeContainRepository.findById(id).ifPresent(notice -> {
            notice.setDeleted(true);
            noticeContainRepository.save(notice);
        });
    }

    @Transactional
    public Notice save(Notice notice) throws FrogException {
        noticeContainRepository.save(notice);
        return noticeContainRepository.findByNoticeId(notice.getId());
    }

    public List<MenuService.Menu> menus(FrogUserDetails frogUserDetails) throws FrogException {
        List<MenuService.Menu> menus = frogUserDetails.getAuthorities().stream()
                .filter(a -> a instanceof FrogUserDetailsService.GrantedAuthorityWithEntity)
                .map(a -> (FrogUserDetailsService.GrantedAuthorityWithEntity) a)
                .map(a -> a.getAuthorityEnum())
                .flatMap(authority -> menuService.menus(authority).stream())
                .distinct()
                .collect(Collectors.toList());
        List<MenuService.Menu> allMenus = menuService.addParent(menus);
        //将父子元素全部放在result中
        List<MenuService.Menu> result = new ArrayList<>();
        for (MenuService.Menu m : allMenus) {
            result.add(m);
            if (m.getChildren() != null) {//如果有子菜单也放入result
                for (MenuService.Menu children : m.getChildren()) {
                    result.add(children);
                }
            }
        }
        return result;
    }

    public Map<String, String> queryMenuMap(FrogUserDetails frogUserDetails) throws FrogException {
        Map<String, String> resultMap = new HashMap<>();
        List<MenuService.Menu> menuList = menus(frogUserDetails);
        menuList.stream().forEach(menu -> resultMap.put(menu.getUrl(), menu.getName()));
        return resultMap;
    }

}
