<script setup lang="ts">
/**
 * 登录页（前后端对齐 `LoginIn`）
 */
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const form = reactive({ username: '', password: '' })
const loading = ref(false)

async function onLogin() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await auth.login({
      username: form.username.trim(),
      password: form.password,
    })
    router.push('/learn')
  } catch (e: any) {
    ElMessage.error(e?.message || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 class="title">AI 伴学</h2>
      <p class="subtitle">登录你的学习空间</p>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="用户名">
          <el-input
            v-model="form.username"
            placeholder="username"
            autocomplete="username"
            @keyup.enter="onLogin"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="password"
            autocomplete="current-password"
            @keyup.enter="onLogin"
          />
        </el-form-item>
        <el-button
          type="primary"
          :loading="loading"
          class="btn"
          @click="onLogin"
        >登录</el-button>
      </el-form>
      <p class="switch">
        还没有账号？<router-link to="/register">去注册</router-link>
      </p>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: var(--color-bg);
}
.login-card {
  width: 380px;
  padding: 16px 24px 24px;
}
.title {
  margin: 0;
}
.subtitle {
  color: var(--color-text-2);
  margin: 4px 0 20px;
}
.btn {
  width: 100%;
  margin-top: 4px;
}
.switch {
  color: var(--color-text-2);
  font-size: 13px;
  text-align: center;
  margin: 14px 0 0;
}
</style>
