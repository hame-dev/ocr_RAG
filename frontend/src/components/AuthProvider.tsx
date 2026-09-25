"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo } from "react";
import {
  UNAUTHORIZED_EVENT,
  User,
  getMe,
  login as apiLogin,
  logout as apiLogout,
} from "@/lib/api";

const ME_KEY = ["me"] as const;

interface AuthCtx {
  user: User | null;
  status: "loading" | "authenticated" | "anonymous";
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>({
  user: null,
  status: "loading",
  login: async () => {
    throw new Error("AuthProvider missing");
  },
  logout: async () => {},
});

export const useAuth = () => useContext(Ctx);

/**
 * Session state for the whole app.
 *
 * This only drives the UI (redirects, the username in the header). Access is
 * enforced by the API, which rejects every request without a valid session.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: getMe,
    staleTime: 5 * 60_000,
    retry: false,
  });

  // Drop every cached response except the session itself, so nothing from the
  // previous user survives a sign-out or an expired session.
  const resetSession = useCallback(
    (user: User | null) => {
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] });
      queryClient.setQueryData(ME_KEY, user);
    },
    [queryClient],
  );

  useEffect(() => {
    const onUnauthorized = () => resetSession(null);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [resetSession]);

  const value = useMemo<AuthCtx>(() => {
    const user = me.data ?? null;
    return {
      user,
      status: me.isPending ? "loading" : user ? "authenticated" : "anonymous",
      login: async (username, password) => {
        const signedIn = await apiLogin(username, password);
        resetSession(signedIn);
        return signedIn;
      },
      logout: async () => {
        try {
          await apiLogout();
        } finally {
          resetSession(null);
        }
      },
    };
  }, [me.data, me.isPending, resetSession]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
