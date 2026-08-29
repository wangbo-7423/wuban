import { http } from './http'
import type { AuthMeOut, LoginIn, RegisterIn, Resp, TokenOut } from './types'

export type LoginResult = TokenOut

export async function login(payload: LoginIn): Promise<LoginResult> {
  const res = await http.post<Resp<TokenOut>>('/auth/login', payload)
  return res.data as unknown as TokenOut
}

export async function register(payload: RegisterIn): Promise<LoginResult> {
  // 注册成功 = 后端直接返回 token，前端自动登录，无需二次 /login
  const res = await http.post<Resp<TokenOut>>('/auth/register', payload)
  return res.data as unknown as TokenOut
}

export async function fetchMe(): Promise<AuthMeOut> {
  const res = await http.get<Resp<AuthMeOut>>('/auth/me')
  return res.data as unknown as AuthMeOut
}
