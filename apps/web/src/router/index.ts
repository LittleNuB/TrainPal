import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import VideoAnalysisView from '@/views/VideoAnalysisView.vue'

export const createTrainPalRoutes = (includeDesignGallery: boolean): RouteRecordRaw[] => {
  const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: HomeView,
    meta: {
      shell: 'top-level',
      theme: 'journal',
      showBottomNav: true,
      showAnalysisTask: true,
      showTrainingTask: true,
      title: '首页',
    },
  },
  {
    path: '/analysis',
    name: 'analysis',
    component: VideoAnalysisView,
    meta: {
      shell: 'immersive',
      theme: 'journal',
      showBottomNav: false,
      showAnalysisTask: false,
      showTrainingTask: true,
      title: '视频分析',
    },
  },
  {
    path: '/plan',
    name: 'plan',
    component: () => import('@/views/PlanDraftView.vue'),
    meta: {
      shell: 'immersive',
      theme: 'journal',
      showBottomNav: false,
      showAnalysisTask: true,
      showTrainingTask: true,
      taskDockAboveAction: true,
      title: '训练方案',
    },
  },
  {
    path: '/personalize',
    name: 'personalize',
    component: () => import('@/views/PersonalizeView.vue'),
    meta: {
      shell: 'immersive',
      theme: 'journal',
      showBottomNav: false,
      showAnalysisTask: true,
      showTrainingTask: true,
      taskDockAboveAction: true,
      title: 'GYMTI 测评',
    },
  },
  {
    path: '/train',
    name: 'train',
    component: () => import('@/views/TrainHubView.vue'),
    meta: {
      shell: 'top-level',
      theme: 'journal',
      showBottomNav: true,
      showAnalysisTask: true,
      showTrainingTask: false,
      title: '训练',
    },
  },
  {
    path: '/training',
    name: 'training',
    component: () => import('@/views/TrainingView.vue'),
    meta: {
      shell: 'immersive',
      theme: 'training',
      showBottomNav: false,
      showAnalysisTask: false,
      showTrainingTask: false,
      title: '训练中',
    },
  },
  {
    path: '/result/:recordId',
    name: 'result',
    component: () => import('@/views/ResultView.vue'),
    meta: {
      shell: 'immersive',
      theme: 'journal',
      showBottomNav: false,
      showAnalysisTask: true,
      showTrainingTask: true,
      title: '训练结果',
    },
  },
  {
    path: '/mine',
    name: 'mine',
    component: () => import('@/views/MineView.vue'),
    meta: {
      shell: 'top-level',
      theme: 'journal',
      showBottomNav: true,
      showAnalysisTask: true,
      showTrainingTask: true,
      title: '我的',
    },
  },
  ]

  if (includeDesignGallery) {
    routes.push({
      path: '/__design/trainpal',
      name: 'trainpal-design-gallery',
      component: () => import('@/views/DesignGalleryView.vue'),
      meta: {
        shell: 'immersive',
        theme: 'journal',
        showBottomNav: false,
        showAnalysisTask: false,
        showTrainingTask: false,
        title: 'TrainPal 设计画廊',
      },
    })
    routes.push({
      path: '/__design/trainpal/pets',
      name: 'trainpal-pet-lab',
      component: () => import('@/views/PetLabView.vue'),
      meta: {
        shell: 'immersive',
        theme: 'journal',
        showBottomNav: false,
        showAnalysisTask: false,
        showTrainingTask: false,
        title: 'TrainPal 七猫动画预览台',
      },
    })
  }

  return routes
}

export const router = createRouter({
  history: createWebHistory(),
  routes: createTrainPalRoutes(import.meta.env.DEV),
  scrollBehavior: () => ({ top: 0 }),
})
