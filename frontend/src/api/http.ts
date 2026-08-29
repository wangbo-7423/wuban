import axios from 'axios'

// axios 实例：baseURL='/api'，请求自动带 token，响应解包统一响应体
export const http = axios.create({ baseURL: '/api', timeout: 10000 })

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

http.interceptors.response.use(
  (res) => res.data, // 后端统一 {code,message,data}
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
