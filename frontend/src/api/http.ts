import axios from 'axios'

// axios 实例：baseURL='/api'，请求自动带 token，响应解包统一响应体
export const http = axios.create({ baseURL: '/api', timeout: 10000 })

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

http.interceptors.response.use(
  (res) => {
    const body = res.data as { code?: number; message?: string } | undefined
    // 后端把业务异常包成 HTTP 200 + {code,message,data}（见 backend/app/core/exceptions.py），
    // HTTP 状态码永远是 200——只看 err.response.status 永远等不到 401，
    // 登录态过期后整个应用会变成「僵尸登录态」：请求全部静默失败、
    // 页面停在旧数据上。这里按业务码判定登录失效。
    if (body?.code === 401) {
      localStorage.removeItem('token')
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
      return Promise.reject(body)
    }
    // 其余非 0 业务码也按错误抛出：调用方的 catch 才能拿到真实 message。
    // 否则会「成功拿到错误信封」——症状如登录失败却跳转、
    // createConversation 返回无 id 的会话对象。
    if (body && typeof body === 'object' && body.code !== undefined && body.code !== 0) {
      return Promise.reject(body)
    }
    return res.data // 后端统一 {code,message,data}
  },
  (err) => {
    // 登录态失效：清 token 并回登录页（用 location 跳转避免循环依赖 router）
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    // 统一错误体，抛给调用方
    const body = err.response?.data || { code: 500, message: '网络错误', data: {} }
    return Promise.reject(body)
  },
)
