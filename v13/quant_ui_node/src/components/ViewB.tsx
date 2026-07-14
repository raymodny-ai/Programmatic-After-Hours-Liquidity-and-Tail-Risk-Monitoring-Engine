'use client';

import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import useSWR from 'swr';
import { fetchApi } from '../lib/api';

interface SurfaceApiResp {
  ok: boolean;
  data?: {
    ticker: string;
    strikes: number[];
    expirations: string[];
    iv: number[][];
    spot: number;
    dte_list: number[];
  };
}

interface Props {
  ticker?: string;
}

export default function ViewB({ ticker = 'SPY' }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const { data: resp } = useSWR<SurfaceApiResp>(
    `/api/v1/options/surface?ticker=${ticker}`,
    (p: string) => fetchApi<SurfaceApiResp>(p),
  );

  useEffect(() => {
    if (!mountRef.current) return;
    const mount = mountRef.current;
    const width = mount.clientWidth;
    const height = mount.clientHeight;

    // 场景初始化
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0e1a);
    scene.fog = new THREE.Fog(0x0a0e1a, 50, 200);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(40, 30, 40);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    mount.appendChild(renderer.domElement);

    // 光源
    const ambient = new THREE.AmbientLight(0x404060, 0.5);
    scene.add(ambient);
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(20, 30, 20);
    scene.add(dirLight);
    const pointLight = new THREE.PointLight(0x22d3ee, 0.5, 100);
    pointLight.position.set(-20, 20, -20);
    scene.add(pointLight);

    // 坐标网格
    const grid = new THREE.GridHelper(60, 20, 0x1e293b, 0x1e293b);
    scene.add(grid);

    // 坐标轴
    const axes = new THREE.AxesHelper(15);
    scene.add(axes);

    // 添加轴标签
    const labelDiv = document.createElement('div');
    labelDiv.className =
      'absolute text-xs text-slate-500 font-mono pointer-events-none';

    // 鼠标交互
    const mouse = { x: 0, y: 0, isDown: false, prevX: 0, prevY: 0 };
    const onMouseDown = (e: MouseEvent) => {
      mouse.isDown = true;
      mouse.prevX = e.clientX;
      mouse.prevY = e.clientY;
    };
    const onMouseUp = () => {
      mouse.isDown = false;
    };
    const onMouseMove = (e: MouseEvent) => {
      if (!mouse.isDown) return;
      const dx = e.clientX - mouse.prevX;
      const dy = e.clientY - mouse.prevY;
      const rotSpeed = 0.005;
      const spherical = new THREE.Spherical().setFromVector3(
        camera.position.clone().sub(new THREE.Vector3(0, 0, 0)),
      );
      spherical.theta -= dx * rotSpeed;
      spherical.phi = Math.max(0.1, Math.min(Math.PI - 0.1, spherical.phi - dy * rotSpeed));
      camera.position.setFromSpherical(spherical);
      camera.lookAt(0, 0, 0);
      mouse.prevX = e.clientX;
      mouse.prevY = e.clientY;
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const scale = e.deltaY > 0 ? 1.05 : 0.95;
      camera.position.multiplyScalar(scale);
    };

    mount.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mouseup', onMouseUp);
    window.addEventListener('mousemove', onMouseMove);
    mount.addEventListener('wheel', onWheel, { passive: false });

    // 动画循环
    let frameId: number;
    let surfaceMesh: THREE.Mesh | null = null;
    const animate = () => {
      frameId = requestAnimationFrame(animate);
      if (!mouse.isDown) {
        // 自动慢速旋转
        const spherical = new THREE.Spherical().setFromVector3(
          camera.position.clone().sub(new THREE.Vector3(0, 0, 0)),
        );
        spherical.theta += 0.002;
        camera.position.setFromSpherical(spherical);
        camera.lookAt(0, 0, 0);
      }
      renderer.render(scene, camera);
    };
    animate();

    // 当数据变化时构建曲面
    const buildSurface = () => {
      if (surfaceMesh) {
        scene.remove(surfaceMesh);
        surfaceMesh.geometry.dispose();
        (surfaceMesh.material as THREE.Material).dispose();
        surfaceMesh = null;
      }
      const surface = resp?.data;
      if (!surface || !surface.iv?.length) return;

      const strikes = surface.strikes;
      const expCount = surface.expirations.length;
      const strikeCount = strikes.length;

      // 归一化坐标
      const minStrike = Math.min(...strikes);
      const maxStrike = Math.max(...strikes);
      const spanStrike = maxStrike - minStrike || 1;
      const minDte = Math.min(...surface.dte_list);
      const maxDte = Math.max(...surface.dte_list);
      const spanDte = maxDte - minDte || 1;

      const geometry = new THREE.BufferGeometry();
      const positions: number[] = [];
      const colors: number[] = [];
      const indices: number[] = [];

      for (let i = 0; i < expCount; i++) {
        for (let j = 0; j < strikeCount; j++) {
          const x = ((strikes[j] - minStrike) / spanStrike) * 40 - 20;
          const z = ((surface.dte_list[i] - minDte) / spanDte) * 40 - 20;
          const y = (surface.iv[i]?.[j] ?? 0) * 100; // 放大 IV
          positions.push(x, y, z);

          // 颜色随 IV 变化：低(蓝) → 中(青) → 高(红)
          const ivVal = surface.iv[i]?.[j] ?? 0;
          const t = Math.min(1, Math.max(0, (ivVal - 0.1) / 0.5));
          const r = Math.floor(t * 244);
          const g = Math.floor((1 - Math.abs(t - 0.5) * 2) * 211);
          const b = Math.floor((1 - t) * 238);
          colors.push(r / 255, g / 255, b / 255);
        }
      }

      for (let i = 0; i < expCount - 1; i++) {
        for (let j = 0; j < strikeCount - 1; j++) {
          const a = i * strikeCount + j;
          const b = a + 1;
          const c = (i + 1) * strikeCount + j;
          const d = c + 1;
          indices.push(a, b, d, a, d, c);
        }
      }

      geometry.setAttribute(
        'position',
        new THREE.Float32BufferAttribute(positions, 3),
      );
      geometry.setAttribute(
        'color',
        new THREE.Float32BufferAttribute(colors, 3),
      );
      geometry.setIndex(indices);
      geometry.computeVertexNormals();

      const material = new THREE.MeshStandardMaterial({
        vertexColors: true,
        side: THREE.DoubleSide,
        flatShading: false,
        roughness: 0.4,
        metalness: 0.2,
      });

      surfaceMesh = new THREE.Mesh(geometry, material);
      scene.add(surfaceMesh);
    };

    buildSurface();

    // 响应式
    const handleResize = () => {
      if (!mount) return;
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    window.addEventListener('resize', handleResize);

    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener('resize', handleResize);
      mount.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mouseup', onMouseUp);
      window.removeEventListener('mousemove', onMouseMove);
      mount.removeEventListener('wheel', onWheel);
      if (surfaceMesh) {
        scene.remove(surfaceMesh);
        surfaceMesh.geometry.dispose();
        (surfaceMesh.material as THREE.Material).dispose();
      }
      renderer.dispose();
      if (mount.contains(renderer.domElement)) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [resp]);

  return (
    <div className="p-6 h-full flex flex-col">
      <header className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-lg font-semibold">
            视图 B · 3D 波动率曲面透视舱
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            鼠标拖动旋转 · 滚轮缩放 · 颜色映射 IV 强度
          </p>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <span>标的</span>
          <select
            defaultValue={ticker}
            className="bg-bg-card border border-slate-700 px-2 py-1 rounded font-mono text-slate-200"
          >
            <option value="SPY">SPY</option>
            <option value="QQQ">QQQ</option>
            <option value="IWM">IWM</option>
          </select>
        </div>
      </header>
      <div
        ref={mountRef}
        className="flex-1 bg-bg-card border border-slate-800 rounded-md relative overflow-hidden"
      />
    </div>
  );
}