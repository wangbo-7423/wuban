<script setup lang="ts">
/**
 * 学习主界面（新）
 * - 顶栏：品牌 / 课程（仅绑定具体课程时显示）/ 抽屉触发按钮 / 用户操作
 * - 主体：左侧「对话抽屉」+ 中间「聊天区」+ 右侧「学习状态抽屉」
 */
import { onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useLearnStore } from '@/stores/learn'
import ChatPanel from '@/components/chat/ChatPanel.vue'
import SidePanel from '@/components/learn/SidePanel.vue'
import ConversationList from '@/components/learn/ConversationList.vue'
import ResizableDrawer from '@/components/common/ResizableDrawer.vue'

const router = useRouter()
const auth = useAuthStore()
const learn = useLearnStore()

onMounted(() => {
  // init 失败（网络/登录失效）：登录失效由 http 拦截器统一跳登录页，
  // 其余静默——侧栏与上下文是辅助信息，不阻塞聊天区
  learn.init().catch(() => {})
  // 启动时同步一次资料：修正 localStorage 里的陈旧昵称，同时校验 token
  auth.refresh().catch(() => {})
})

const displayName = computed(() => auth.nickname || auth.role || '同学')
// "general"/缺失 = 未绑定具体课程的综合模式，不是课程名，顶栏不展示。
// 刻意不展示「进度/掌握度」类数字（docs/00 §6：探索循环，不是测验循环，
// 过程性深度估计值不适合以百分数形式呈现）；复习入口收在右侧学习状态抽屉。
const courseName = computed(() => learn.context?.course?.name || '')
const hasCourse = computed(() => {
  const id = learn.context?.course?.id || ''
  return !!courseName.value && id !== '' && id !== 'general'
})
</script>

<template>
  <div class="learn-home">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="left">
        <button
          class="icon-btn"
          :class="{ active: learn.leftDrawer.open }"
          :title="learn.leftDrawer.open ? '收起对话列表' : '展开对话列表'"
          aria-label="对话列表"
          @click="learn.toggleLeft()"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <path
              d="M3 6h18M3 12h18M3 18h18"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
            />
          </svg>
        </button>
        <div class="brand">
          AI 伴学
          <span class="tag">工科认知引导</span>
        </div>
      </div>

      <div class="center">
        <span v-if="hasCourse" class="course-name">{{ courseName }}</span>
      </div>

      <div class="right">
        <button
          class="icon-btn"
          :class="{ active: learn.rightDrawer.open }"
          :title="learn.rightDrawer.open ? '收起学习状态' : '展开学习状态'"
          aria-label="学习状态"
          @click="learn.toggleRight()"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <path
              d="M4 20V10M10 20V4M16 20v-7M22 20H2"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
            />
          </svg>
        </button>
        <el-dropdown trigger="click">
          <div class="user-mini">
            <span class="avatar">{{ displayName.slice(0, 1) }}</span>
            <span class="uname">{{ displayName }}</span>
            <svg class="caret-icon" width="10" height="10" viewBox="0 0 12 12">
              <path
                d="M3 5l3 3 3-3"
                stroke="currentColor"
                stroke-width="1.5"
                fill="none"
                stroke-linecap="round"
              />
            </svg>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>
                {{ displayName }}（{{ auth.role || 'student' }}）
              </el-dropdown-item>
              <el-dropdown-item @click="learn.toggleRight(true)">
                查看学习状态
              </el-dropdown-item>
              <el-dropdown-item
                divided
                @click="auth.logout(); router.push('/login')"
              >退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <!-- 主体 -->
    <div class="body">
      <!-- 左侧对话抽屉 -->
      <ResizableDrawer
        side="left"
        :open="learn.leftDrawer.open"
        :width="learn.leftDrawer.width"
        :min="learn.leftDrawer.min"
        :max="learn.leftDrawer.max"
        @update:open="learn.toggleLeft($event)"
        @update:width="learn.resizeLeft($event)"
        @close="learn.toggleLeft(false)"
      >
        <template #header>
          <h3>对话</h3>
        </template>
        <ConversationList />
      </ResizableDrawer>

      <!-- 主聊天区 -->
      <main class="chat-area">
        <ChatPanel
          @open-left="learn.toggleLeft(true)"
          @open-right="learn.toggleRight(true)"
        />
      </main>

      <!-- 右侧学习状态抽屉 -->
      <ResizableDrawer
        side="right"
        :open="learn.rightDrawer.open"
        :width="learn.rightDrawer.width"
        :min="learn.rightDrawer.min"
        :max="learn.rightDrawer.max"
        @update:open="learn.toggleRight($event)"
        @update:width="learn.resizeRight($event)"
        @close="learn.toggleRight(false)"
      >
        <template #header>
          <h3>学习状态</h3>
        </template>
        <SidePanel />
      </ResizableDrawer>
    </div>
  </div>
</template>

<style scoped>
.learn-home {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--color-bg);
}

/* ---------- 顶栏 ---------- */
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 14px;
  background: #fff;
  border-bottom: 1px solid #eceff3;
  flex: none;
  gap: 12px;
  z-index: 10;
}
.topbar .left,
.topbar .right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.topbar .center {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.brand {
  font-weight: 700;
  font-size: 16px;
}
.brand .tag {
  font-size: 11px;
  font-weight: 500;
  color: var(--color-primary);
  background: #eef3ff;
  border-radius: 999px;
  padding: 2px 8px;
  margin-left: 6px;
}
.course-name {
  color: var(--color-text-2);
  font-size: 13px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.icon-btn {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--color-text-2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.icon-btn:hover {
  background: #f1f4f9;
  color: var(--color-text);
}
.icon-btn.active {
  background: #eef3ff;
  color: var(--color-primary);
  border-color: #dbe6ff;
}

/* ---------- 顶部用户 ---------- */
.user-mini {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px 4px 4px;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.15s;
}
.user-mini:hover {
  background: #f1f4f9;
}
.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: linear-gradient(135deg, #3478f6 0%, #8b5cf6 100%);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
}
.uname {
  font-size: 13px;
  max-width: 120px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.caret-icon {
  color: var(--color-text-2);
}

/* ---------- 主体 ---------- */
.body {
  display: flex;
  flex: 1;
  min-height: 0;
  position: relative;
}
.chat-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
</style>
