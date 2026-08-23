package com.shuyiwa.fitness.gateway.security;

import java.security.PublicKey;

/** 为 AgentContext RS256 验签提供按 kid 查找公钥的统一边界。 */
public interface AgentContextPublicKeyProvider {

    /**
     * 返回指定 kid 的公钥；没有配置或无法安全取得时返回 null/抛出安全异常，调用方必须拒绝请求。
     */
    PublicKey getPublicKey(String keyId);
}
