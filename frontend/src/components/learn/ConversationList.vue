<script setup lang="ts">
/**
 * 对话列表（抽屉主体）：
 * - 顶部"新建对话"按钮
 * - 历史对话列表（可滚动）
 * - 底部用户信息 + 设置 + 退出登录
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
// 注意：ElMessage / ElMessageBox 不要手动 import——本项目用 unplugin 按需自动引入，
// 手动 import 会绕过样式注入，导致 ElMessageBox 确认框无样式、被渲染到视口外（删除流程因此失效）。
import { useAuthStore } from '@/stores/auth'
import { useLearnStore } from '@/stores/learn'

const learn = useLearnStore()
const auth = useAuthStore()
const router = useRouter()

const displayName = computed(() => auth.nickname || auth.role || '同学')
const initial = computed(() => displayName.value.slice(0, 1))

function fmt(t: string) {
  try {
    const d = new Date(t)
    return `${d.getMonth() + 1}/${d.getDate()}`
  } catch {
    return ''
  }
}

function select(id: string) {
  learn.selectConversation(id)
}
function newConv() {
  learn.newConversation()
}

function logout() {
  auth.logout()
  router.push('/login')
}

function openSettings() {
  // 预留：将来弹出账号设置弹窗
}

// ── 会话操作菜单（⋯）────────────────────────────────────────
/** 记录当前展开的菜单，好让「⋯」在菜单打开期间保持可见 */
const openMenuId = ref<string | null>(null)

function onMenuVisible(id: string, visible: boolean) {
  openMenuId.value = visible ? id : null
}

function onCommand(cmd: { action: string; id: string }) {
  if (cmd.action === 'delete') void confirmDelete(cmd.id)
}

async function confirmDelete(id: string) {
  const name = learn.conversations.find((c) => c.id === id)?.title || '该对话'
  try {
    await ElMessageBox.confirm(
      `确定删除「${name}」吗？对话中的消息会一并删除，且无法恢复。`,
      '删除对话',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return // 用户取消
  }
  try {
    await learn.removeConversation(id)
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error((e as Error)?.message || '删除失败，请重试')
  }
}
</script>

<template>
  <div class="conv-pane">
    <div class="head">
      <button class="new-btn" @click="newConv">
        <span class="plus">＋</span>
        <span>新建对话</span>
      </button>
    </div>
    <div class="title">历史对话</div>
    <ul class="items">
      <li
        v-for="c in learn.conversations"
        :key="c.id"
        class="item"
        :class="{ active: c.id === learn.activeId }"
        @click="select(c.id)"
      >
        <span class="dot" />
        <div class="meta">
          <div class="name">{{ c.title || '未命名对话' }}</div>
          <div class="time">{{ fmt(c.updated_at) }}</div>
        </div>

        <el-dropdown
          trigger="click"
          placement="bottom-end"
          @command="onCommand"
          @visible-change="(v: boolean) => onMenuVisible(c.id, v)"
        >
          <button
            class="more-btn"
            :class="{ show: openMenuId === c.id }"
            aria-label="更多操作"
            title="更多操作"
            @click.stop
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="5" cy="12" r="1.8" />
              <circle cx="12" cy="12" r="1.8" />
              <circle cx="19" cy="12" r="1.8" />
            </svg>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item :command="{ action: 'delete', id: c.id }">
                <span class="danger-item">删除对话</span>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </li>
      <li v-if="!learn.conversations.length" class="empty">
        还没有历史对话，点击上方"新建对话"开始 ↗
      </li>
    </ul>

    <div class="footer-spacer" />

    <div class="user-card">
      <div class="user-info" @click="openSettings">
        <span class="avatar">{{ initial }}</span>
        <div class="uname">
          <div class="name">{{ displayName }}</div>
          <div class="role">{{ auth.role || 'student' }}</div>
        </div>
      </div>
      <el-tooltip content="退出登录" placement="top">
        <button class="logout-btn" aria-label="退出登录" @click="logout">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path
              d="M15 12H4M4 12l4-4M4 12l4 4"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
            <path
              d="M14 4h5a1 1 0 011 1v14a1 1 0 01-1 1h-5"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
      </el-tooltip>
    </div>
  </div>
</template>

<style scoped>
.conv-pane {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 12px;
  gap: 8px;
}
.head {
  margin-bottom: 4px;
}
.new-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  height: 38px;
  border: none;
  background: linear-gradient(135deg, #3478f6 0%, #5e8bff 100%);
  color: #fff;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(52, 120, 246, 0.25);
  transition: transform 0.15s, box-shadow 0.15s;
}
.new-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 18px rgba(52, 120, 246, 0.32);
}
.plus {
  font-size: 16px;
  line-height: 1;
}
.title {
  font-size: 11px;
  letter-spacing: 1px;
  color: var(--color-text-2);
  margin: 6px 4px;
  text-transform: uppercase;
}
.items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}
.item:hover {
  background: #f1f4f9;
}
.item.active {
  background: #e8f0ff;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #cbd5e1;
  flex: none;
}
.item.active .dot {
  background: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(52, 120, 246, 0.18);
}
.meta {
  min-width: 0;
  flex: 1;
}
.name {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.time {
  font-size: 11px;
  color: var(--color-text-2);
}
.more-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  color: var(--color-text-2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  /* 默认隐藏：hover 或菜单展开时才显形，避免列表视觉噪音 */
  opacity: 0;
  transition: opacity 0.15s, background 0.15s, color 0.15s;
}
.item:hover .more-btn,
.more-btn.show,
.more-btn:focus-visible {
  opacity: 1;
}
.more-btn:hover {
  background: #e6eaf2;
  color: #1f2937;
}
.danger-item {
  color: #b91c1c;
}
.empty {
  padding: 12px;
  font-size: 12px;
  color: var(--color-text-2);
  text-align: center;
  background: #f6f7fb;
  border-radius: 8px;
}
.footer-spacer {
  flex: 1;
}
.user-card {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px;
  margin-top: 6px;
  border-top: 1px solid #eceff3;
  background: #fff;
  border-radius: 10px;
}
.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 6px;
  transition: background 0.15s;
}
.user-info:hover {
  background: #f1f4f9;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(135deg, #3478f6 0%, #8b5cf6 100%);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  flex: none;
}
.uname {
  min-width: 0;
}
.uname .name {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.uname .role {
  font-size: 11px;
  color: var(--color-text-2);
}
.logout-btn {
  width: 30px;
  height: 30px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  color: var(--color-text-2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
  flex: none;
}
.logout-btn:hover {
  background: #fee2e2;
  color: #b91c1c;
}
</style>
