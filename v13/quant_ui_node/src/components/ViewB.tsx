'use client';

import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import useSWR from 'swr';
import { fetchApi } from '../lib/api';

// V1.3 /api/v1/options/surface/{ticker} 返回 {ticker, as_of_date, expirations, strikes, iv_surface, oi_surface, volume_surface, data_quality, ...}
interface V13Surface {
  ticker: string;
  as_of_date: string;
  expirations: string[];
  strikes: number[];
  iv_surface: number[][];
  oi_surface: number[][];
  volume_surface: number[][];
  data_quality: string;
}

interface Props {
  ticker?: string;
}

export default function ViewB({ ticker = 'SPY' }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const [webglSupported, setWebglSupported] = useState<boolean | null>(null);

  // V1.3 用 path param: /api/v1/options/surface/{ticker}
  const { data: surface, isLoading } = useSWR<V13Surface | null>(
    `/api/v1/options/surface/${ticker}`,
    async (p: string) => {
      try {
        return await fetchApi<V13Surface>(p);
      } catch (err) {
        console.warn(`[ViewB] surface fetch failed for ${ticker}:`, err);
        return null;
      }
    },
    { revalidateOnFocus: false },
  );

  // 检测 WebGL 支持 — 没就 fallback (server/老浏览器/headless chrome 都没)
  useEffect(() => {
    try {
      const c = document.createElement('canvas');
      const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      setWebglSupported(!!gl);
    } catch {
      setWebglSupported(false);
    }
  }, []);

  useEffect(() => {
    // 只有 surface 数据非空 + WebGL 可用时才尝试创建 Three.js scene
    if (!mountRef.current) return;
    if (!surface?.strikes?.length || !surface?.iv_surface?.length) return;
    if (!webglSupported) return;

    const mount = mountRef.current;
    const width = mount.clientWidth;
    const height = mount.clientHeight;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0e1a);
    scene.fog = new THREE.Fog(0x0a0e1a, 50, 200);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(40, 30, 40);
    camera.lookAt(0, 0, 0);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch (err) {
      console.warn('[ViewB] WebGLRenderer init failed:', err);
      setWebglSupported(false);
      return;
    }
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    mount.appendChild(renderer.domElement);

    const ambient = new THREE.AmbientLight(0xffffff, 0.5);
    scene.add(ambient);
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(20, 30, 20);
    scene.add(dirLight);

    const grid = new THREE.GridHelper(60, 20, 0x1e293b, 0x1e293b);
    scene.add(grid);
    const axes = new THREE.AxesHelper(15);
    scene.add(axes);

    const mouse = { x: 0, y: 0, isDown: false, prevX: 0, prevY: 0 };
    const onMouseDown = (e: MouseEvent) => { mouse.isDown = true; mouse.prevX = e.clientX; mouse.prevY = e.clientY; };
    const onMouseUp = () => { mouse.isDown = false; };
    const onMouseMove = (e: MouseEvent) => {
      if (!mouse.isDown) return;
      const dx = e.clientX - mouse.prevX;
      const dy = e.clientY - mouse.prevY;
      const rotSpeed = 0.005;
      const spherical = new THREE.Spherical().setFromVector3(camera.position.clone().sub(new THREE.Vector3(0, 0, 0)));
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

    let frameId: number;
    let surfaceMesh: THREE.Mesh | null = null;
    const animate = () => {
      frameId = requestAnimationFrame(animate);
      if (!mouse.isDown) {
        const spherical = new THREE.Spherical().setFromVector3(camera.position.clone().sub(new THREE.Vector3(0, 0, 0)));
        spherical.theta += 0.002;
        camera.position.setFromSpherical(spherical);
        camera.lookAt(0, 0, 0);
      }
      renderer.render(scene, camera);
    };
    animate();

    const buildSurface = () => {
      if (!surface) return;
      if (surfaceMesh) {
        scene.remove(surfaceMesh);
        surfaceMesh.geometry.dispose();
        (surfaceMesh.material as THREE.Material).dispose();
        surfaceMesh = null;
      }

      const strikes = surface.strikes;
      const expCount = surface.expirations.length;
      const strikeCount = strikes.length;
      if (strikeCount === 0 || expCount === 0) return;
      const minStrike = Math.min(...strikes);
      const maxStrike = Math.max(...strikes);
      const spanStrike = maxStrike - minStrike || 1;

      const geometry = new THREE.BufferGeometry();
      const positions: number[] = [];
      const colors: number[] = [];
      const indices: number[] = [];

      for (let i = 0; i < expCount; i++) {
        for (let j = 0; j < strikeCount; j++) {
          const x = ((strikes[j] - minStrike) / spanStrike) * 40 - 20;
          const z = (i / Math.max(1, expCount - 1)) * 40 - 20;
          const y = (surface.iv_surface[i]?.[j] ?? 0) * 100;
          positions.push(x, y, z);
          const ivVal = surface.iv_surface[i]?.[j] ?? 0;
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
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
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
      if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement);
    };
  }, [surface, webglSupported]);

  const showNoData = !isLoading && (!surface || !surface.strikes?.length);
  const showWebGLMissing = !isLoading && webglSupported === false;

  return (
    <div className="p-6 h-full flex flex-col">
      <header className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-lg font-semibold">视图 B · 3D 波动率曲面透视舱</h1>
          <p className="text-xs text-slate-500 mt-1">
            鼠标拖动旋转 · 滚轮缩放 · 颜色映射 IV 强度
          </p>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <span>标的</span>
          <select defaultValue={ticker} className="bg-bg-card border border-slate-700 px-2 py-1 rounded font-mono text-slate-200">
            <option value="SPY">SPY</option>
            <option value="QQQ">QQQ</option>
            <option value="IWM">IWM</option>
          </select>
        </div>
      </header>
      <div className="flex-1 bg-bg-card border border-slate-800 rounded-md overflow-hidden relative">
        <div ref={mountRef} className="w-full h-full" />
        {showNoData && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-500 text-sm gap-2 pointer-events-none">
            <div>无 surface 数据 ({ticker})</div>
            <div className="text-[10px] text-slate-600 max-w-md text-center">
              V1.3 surface 接口需全 OI/volume 数据,本环境未启用 — 需要 paid Polygon + 完整 option chain 拉取
            </div>
          </div>
        )}
        {showWebGLMissing && (
          <div className="absolute bottom-2 right-2 text-[10px] text-amber-500/70 pointer-events-none">
            ⚠ 浏览器无 WebGL,曲面无法渲染 (3D 视图)
          </div>
        )}
        {isLoading && (
          <div className="absolute top-2 right-2 text-[10px] text-slate-500 pointer-events-none">loading surface…</div>
        )}
      </div>
    </div>
  );
}