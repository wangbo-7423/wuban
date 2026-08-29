import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/learn' },
    { path: '/login', component: () => import('@/views/Login.vue') },
    { path: '/register', component: () => import('@/views/Register.vue') },
    { path: '/learn', component: () => import('@/views/learn/LearnHome.vue') },
  ],
})

// 简单鉴权守卫：未登录跳登录页（login/register 公开）
router.beforeEach((to) => {
  const authed = !!localStorage.getItem('token')
  if (to.path !== '/login' && to.path !== '/register' && !authed) return '/login'
  if ((to.path === '/login' || to.path === '/register') && authed) return '/learn'
  return true
})

export default router
