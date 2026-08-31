<script setup lang="ts">
/**
 * ArtifactGallery：code_runner 运行产物展示（当前支持图片）。
 * 学生跑完 matplotlib 出图后，图直接显示在卡内，点击可放大查看。
 */
import { computed, ref } from 'vue'
import type { EngineeringArtifact } from '@/api/types'

const props = defineProps<{ artifacts: EngineeringArtifact[] }>()

const images = computed(() =>
  props.artifacts.filter((a) => (a.type === 'image' || /\.(png|jpe?g|gif|webp|svg)$/i.test(a.url || '')) && a.url),
)

const zoomed = ref<string | null>(null)
function open(url: string) {
  zoomed.value = url
}
function close() {
  zoomed.value = null
}
function label(name: string | null | undefined, i: number) {
  return name || `运行结果 ${i + 1}`
}
</script>

<template>
  <div v-if="images.length" class="gallery">
    <div class="g-label">运行结果</div>
    <div class="g-grid">
      <figure v-for="(a, i) in images" :key="a.url" class="g-item">
        <img :src="a.url!" :alt="label(a.name, i)" loading="lazy" @click="open(a.url!)" />
        <figcaption>{{ label(a.name, i) }}</figcaption>
      </figure>
    </div>

    <div v-if="zoomed" class="lightbox" @click="close">
      <img :src="zoomed" alt="运行结果放大图" @click.stop />
      <button class="close" aria-label="关闭" @click.stop="close">×</button>
    </div>
  </div>
</template>

<style scoped>
.gallery {
  margin-top: 8px;
}
.g-label {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
  user-select: none;
}
.g-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.g-item {
  margin: 0;
}
.g-item img {
  display: block;
  max-width: 280px;
  max-height: 200px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #fff;
  cursor: zoom-in;
}
.g-item figcaption {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 4px;
  text-align: center;
}
.lightbox {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(15, 23, 42, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: zoom-out;
}
.lightbox img {
  max-width: 88vw;
  max-height: 88vh;
  border-radius: 10px;
  background: #fff;
  cursor: default;
}
.close {
  position: absolute;
  top: 16px;
  right: 20px;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
}
.close:hover {
  background: rgba(255, 255, 255, 0.3);
}
</style>
