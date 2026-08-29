<script setup lang="ts">
/**
 * 注册页（前后端对齐 `RegisterIn`）
 *
 * 后端 `schemas/auth.py` 契约：
 *   - 只允许 `username + nickname + password`
 *   - role 固定 student（不开教师端）
 *   - 无 email 字段
 *
 * 前端做：表单 + 客户端二次校验 + 注册后自动登录。
 */
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const form = reactive({
  username: '',
  nickname: '',
  password: '',
  confirm: '',
})
const loading = ref(false)

const USERNAME_RE = /^[a-zA-Z][a-zA-Z0-9_]*$/

function validate(): string | null {
  if (!form.username) return '请输入用户名'
  if (form.username.length < 3 || form.username.length > 32)
    return '用户名长度需在 3~32 之间'
  if (!USERNAME_RE.test(form.username))
    return '用户名仅允许字母、数字、下划线，且必须以字母开头'

  if (!form.nickname) return '请输入昵称'
  if (form.nickname.length > 32) return '昵称最长 32 个字符'

  if (!form.password) return '请输入密码'
  if (form.password.length < 8) return '密码至少 8 位'
  if (form.password.length > 128) return '密码过长'

  if (form.password !== form.confirm) return '两次密码不一致'
  return null
}

async function onRegister() {
  const err = validate()
  if (err) {
    ElMessage.warning(err)
    return
  }
  loading.value = true
  try {
    await auth.register({
      username: form.username.trim(),
      nickname: form.nickname.trim(),
      password: form.password,
    })
    ElMessage.success('注册成功，已自动登录')
    router.push('/learn')
  } catch (e: any) {
    ElMessage.error(e?.message || '注册失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="register-wrap">
    <el-card class="register-card">
      <h2 class="title">注册 AI 伴学</h2>
      <p class="subtitle">理工科一对一陪伴式学习助手</p>

      <el-form label-position="top" @submit.prevent>
        <el-form-item label="用户名">
          <el-input
            v-model="form.username"
            placeholder="字母开头，3~32 位字母/数字/下划线"
            autocomplete="username"
            maxlength="32"
          />
        </el-form-item>

        <el-form-item label="昵称">
          <el-input
            v-model="form.nickname"
            placeholder="希望被称呼的名字"
            maxlength="32"
          />
        </el-form-item>

        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="至少 8 位"
            autocomplete="new-password"
          />
        </el-form-item>

        <el-form-item label="确认密码">
          <el-input
            v-model="form.confirm"
            type="password"
            show-password
            placeholder="再次输入密码"
            autocomplete="new-password"
          />
        </el-form-item>

        <el-button
          type="primary"
          :loading="loading"
          class="btn"
          @click="onRegister"
        >注册并开始学习</el-button>
      </el-form>

      <p class="switch">
        已有账号？<router-link to="/login">去登录</router-link>
      </p>
    </el-card>
  </div>
</template>

<style scoped>
.register-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: var(--color-bg);
}
.register-card {
  width: 400px;
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
