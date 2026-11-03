import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { backendConfigured, mobileApi } from "../network/client";
import { clearPending } from "../network/pending";

type AccountValue = {
  configured: boolean;
  ready: boolean;
  loggedIn: boolean;
  login: (
    username: string,
    password: string,
    organization: string,
  ) => Promise<void>;
  logout: () => Promise<void>;
};
const Context = createContext<AccountValue | null>(null);

export function AccountProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [loggedIn, setLoggedIn] = useState(false);
  useEffect(() => mobileApi.subscribe(setLoggedIn), []);
  useEffect(() => {
    let alive = true;
    mobileApi
      .restore()
      .then((value) => {
        if (alive) setLoggedIn(value);
      })
      .catch(() => {
        if (alive) setLoggedIn(false);
      })
      .finally(() => {
        if (alive) setReady(true);
      });
    return () => {
      alive = false;
    };
  }, []);
  const login = useCallback(
    async (username: string, password: string, organization: string) => {
      await mobileApi.login(username, password, organization);
      setLoggedIn(true);
    },
    [],
  );
  const logout = useCallback(async () => {
    const scope = mobileApi.scope;
    try {
      await mobileApi.logout();
    } finally {
      try {
        if (scope) await clearPending(scope);
      } finally {
        setLoggedIn(mobileApi.loggedIn);
      }
    }
  }, []);
  const value = useMemo(
    () => ({ configured: backendConfigured(), ready, loggedIn, login, logout }),
    [ready, loggedIn, login, logout],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useAccount() {
  const value = useContext(Context);
  if (!value) throw new Error("AccountProvider is missing");
  return value;
}
