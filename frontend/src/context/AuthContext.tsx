import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getOrganizationId, getToken, setOrganizationId, setToken } from "../lib/api";
import type { User } from "../lib/types";

interface AuthApi {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (payload: {
    name: string;
    email: string;
    password: string;
    organization_name: string;
  }) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthApi | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [hasToken, setHasToken] = useState<boolean>(() => Boolean(getToken()));

  const meQuery = useQuery({
    queryKey: ["me", hasToken],
    queryFn: api.auth.me,
    enabled: hasToken,
    retry: false,
    staleTime: 60_000,
  });

  const logout = useCallback(() => {
    setToken(null);
    setOrganizationId(null);
    setHasToken(false);
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    const handler = () => logout();
    window.addEventListener("auth:expired", handler);
    return () => window.removeEventListener("auth:expired", handler);
  }, [logout]);

  useEffect(() => {
    if (!meQuery.isError || !hasToken) return;
    const status = (meQuery.error as { status?: number })?.status;
    if (status === 401) logout();
  }, [meQuery.isError, meQuery.error, hasToken, logout]);

  const user = meQuery.data ?? null;

  useEffect(() => {
    if (!user?.organizations?.length) return;
    const current = getOrganizationId();
    if (!current || !user.organizations.some((org) => org.id === current)) {
      setOrganizationId(user.organizations[0].id);
    }
  }, [user]);

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await api.auth.login(email, password);
      setToken(result.access_token);
      setOrganizationId(result.user.organizations[0]?.id ?? null);
      queryClient.setQueryData(["me", true], result.user);
      setHasToken(true);
    },
    [queryClient],
  );

  const register = useCallback(
    async (payload: { name: string; email: string; password: string; organization_name: string }) => {
      const result = await api.auth.register(payload);
      setToken(result.access_token);
      setOrganizationId(result.user.organizations[0]?.id ?? null);
      queryClient.setQueryData(["me", true], result.user);
      setHasToken(true);
    },
    [queryClient],
  );

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ["me"] });
  }, [queryClient]);

  const value = useMemo<AuthApi>(
    () => ({
      user,
      loading: hasToken && meQuery.isLoading,
      login,
      register,
      logout,
      refresh,
    }),
    [user, hasToken, meQuery.isLoading, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthApi {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
