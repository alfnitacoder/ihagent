import React, { useEffect, useMemo, useRef } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Environment } from '@react-three/drei/core/Environment.js';
import { Lightformer } from '@react-three/drei/core/Lightformer.js';
import * as THREE from 'three';
import { getController, subscribe } from './controller';
import { PALETTE, STATES } from './states';

const LED = {};
const RING = {};
STATES.forEach((name) => {
  LED[name] = new THREE.Color(PALETTE[name].led);
  RING[name] = new THREE.Color(PALETTE[name].ring);
});

function damp(current, target, lambda, dt) {
  return current + (target - current) * (1 - Math.exp(-lambda * dt));
}

function FrameDriver() {
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => subscribe(() => invalidate()), [invalidate]);
  return null;
}

function Look() {
  useFrame((state) => {
    state.camera.lookAt(0, 0.12, 0);
  });
  return null;
}

function useGlowTexture() {
  return useMemo(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 128;
    canvas.height = 128;
    const ctx = canvas.getContext('2d');
    const grad = ctx.createRadialGradient(64, 64, 4, 64, 64, 62);
    grad.addColorStop(0, 'rgba(90, 190, 255, 0.55)');
    grad.addColorStop(0.45, 'rgba(60, 150, 255, 0.16)');
    grad.addColorStop(1, 'rgba(60, 150, 255, 0)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 128, 128);
    const tex = new THREE.CanvasTexture(canvas);
    tex.needsUpdate = true;
    return tex;
  }, []);
}

function RobotRig() {
  const floatRef = useRef(null);
  const headRef = useRef(null);
  const ringSpin = useRef(null);
  const glowRef = useRef(null);
  const eyeL = useRef(null);
  const eyeR = useRef(null);
  const pupilL = useRef(null);
  const pupilR = useRef(null);
  const mouthRef = useRef(null);
  const scanRef = useRef(null);
  const antennaRef = useRef(null);
  const chestRef = useRef(null);
  const earL = useRef(null);
  const earR = useRef(null);
  const shoulderL = useRef(null);
  const shoulderR = useRef(null);
  const elbowL = useRef(null);
  const elbowR = useRef(null);
  const underLight = useRef(null);
  const blink = useRef({ next: 2.2, phase: -1 });
  const glowMap = useGlowTexture();

  const ceramic = useMemo(() => new THREE.MeshPhysicalMaterial({
    color: new THREE.Color('#f6f8fb'),
    roughness: 0.18,
    metalness: 0.04,
    clearcoat: 1,
    clearcoatRoughness: 0.08,
    reflectivity: 0.55,
    sheen: 0.35,
    sheenColor: new THREE.Color('#d5e6ff'),
    envMapIntensity: 1.15,
  }), []);
  const glass = useMemo(() => new THREE.MeshPhysicalMaterial({
    color: new THREE.Color('#07090e'),
    roughness: 0.06,
    metalness: 0.92,
    clearcoat: 1,
    clearcoatRoughness: 0.03,
    envMapIntensity: 1.4,
  }), []);
  const ledMat = useMemo(() => new THREE.MeshBasicMaterial({
    color: LED.idle.clone(),
    toneMapped: false,
  }), []);
  const ringMat = useMemo(() => new THREE.MeshBasicMaterial({
    color: RING.idle.clone(),
    toneMapped: false,
    transparent: true,
    opacity: 0.95,
  }), []);
  const rimMat = useMemo(() => new THREE.MeshBasicMaterial({
    color: RING.idle.clone(),
    toneMapped: false,
    transparent: true,
    opacity: 0.55,
  }), []);

  useEffect(() => () => {
    ceramic.dispose();
    glass.dispose();
    ledMat.dispose();
    ringMat.dispose();
    rimMat.dispose();
    glowMap.dispose();
  }, [ceramic, glass, ledMat, ringMat, rimMat, glowMap]);

  useFrame((state, dt) => {
    const step = Math.min(dt || 0.016, 0.05);
    const c = getController();
    const st = c.state;
    const reduced = !!c.reducedMotion;
    const motion = reduced ? 0 : 1;
    const age = Math.max(0, (performance.now() - c.enteredAt) / 1000);
    const t = state.clock.elapsedTime;
    const k = reduced ? 1 : 1 - Math.exp(-7 * step);

    ledMat.color.lerp(LED[st] || LED.idle, k);
    ringMat.color.lerp(RING[st] || RING.idle, k);
    rimMat.color.lerp(RING[st] || RING.idle, k);

    let level = null;
    if ((st === 'listening' || st === 'speaking') && typeof c.readAudio === 'function') {
      const sample = c.readAudio();
      if (typeof sample === 'number' && Number.isFinite(sample)) level = Math.min(1, Math.max(0, sample));
    } else if (typeof c.audioLevel === 'number') {
      level = c.audioLevel;
    }

    const bob = Math.sin(t * (st === 'working' ? 2.4 : 1.35)) * (st === 'success' ? 0.02 : 0.055) * motion;
    const bounce = st === 'success'
      ? Math.abs(Math.sin(Math.min(age, 0.9) * 11)) * Math.exp(-age * 1.35) * 0.16 * (reduced ? 0.25 : 1)
      : 0;
    if (floatRef.current) {
      floatRef.current.position.y = bob + bounce;
      const sway = st === 'idle' ? Math.sin(t * 0.45) * 0.12 : st === 'working' ? Math.sin(t * 1.1) * 0.08 : 0;
      floatRef.current.rotation.y = damp(floatRef.current.rotation.y, sway * motion, 2.2, step);
    }

    if (headRef.current) {
      const tiltZ = st === 'thinking' ? 0.28 : st === 'error' ? -0.22 : st === 'listening' ? 0.08 : st === 'speaking' ? Math.sin(t * 1.6) * 0.05 * motion : 0;
      const tiltX = st === 'thinking' ? 0.16 : st === 'error' ? 0.1 : st === 'listening' ? -0.04 : 0;
      const yaw = st === 'listening' || st === 'speaking'
        ? c.pointerX * 0.18 * motion
        : Math.sin(t * (st === 'thinking' ? 0.9 : 0.4)) * (st === 'idle' ? 0.22 : 0.12) * motion;
      headRef.current.rotation.z = damp(headRef.current.rotation.z, tiltZ, 5, step);
      headRef.current.rotation.x = damp(headRef.current.rotation.x, tiltX, 5, step);
      headRef.current.rotation.y = damp(headRef.current.rotation.y, yaw, 4, step);
    }

    const speeds = { idle: 0.45, thinking: 1.35, listening: 0.7, speaking: 0.65, working: 2.35, success: 1.7, error: 0.35 };
    if (ringSpin.current) {
      ringSpin.current.rotation.y += step * (speeds[st] || 0.4) * (reduced ? 0.2 : 1);
    }
    if (glowRef.current) {
      const pulse = 0.22 + (PALETTE[st].glow * 0.12) * (0.65 + 0.35 * Math.sin(t * (st === 'error' ? 3.2 : 1.6)));
      glowRef.current.material.opacity = reduced ? 0.28 : pulse;
    }
    if (underLight.current) {
      underLight.current.color.lerp(RING[st] || RING.idle, k);
      const base = st === 'success' ? 6 : st === 'error' ? 2.2 : 3.4;
      underLight.current.intensity = base + Math.sin(t * 2) * (reduced ? 0 : 0.4);
    }

    if (blink.current.phase < 0 && t > blink.current.next && st !== 'success' && st !== 'error') {
      blink.current.phase = 0;
    }
    let blinkAmt = 0;
    if (blink.current.phase >= 0) {
      blink.current.phase += step;
      const p = blink.current.phase;
      blinkAmt = p < 0.06 ? p / 0.06 : p < 0.14 ? 1 : p < 0.22 ? 1 - (p - 0.14) / 0.08 : 0;
      if (p >= 0.22) {
        blink.current.phase = -1;
        blink.current.next = t + 2.4 + Math.random() * 2.8;
      }
    }
    if (reduced) blinkAmt = 0;
    const eyeScaleY = (st === 'success' ? 0.42 : 1) * (1 - 0.88 * blinkAmt);
    const listenBoost = st === 'listening' && level != null ? level * 0.22 : 0;
    const eyeScaleX = st === 'thinking' ? 1.25 : st === 'listening' ? 1.12 + listenBoost : 1;
    [eyeL, eyeR].forEach((ref) => {
      if (!ref.current) return;
      ref.current.scale.y = damp(ref.current.scale.y, Math.max(0.08, eyeScaleY), 14, step);
      ref.current.scale.x = damp(ref.current.scale.x, eyeScaleX, 8, step);
    });

    const lookX = (st === 'thinking' ? Math.sin(t * 3.1) * 0.028 : c.pointerX * 0.02) * motion;
    const lookY = (st === 'error' ? -0.012 : st === 'thinking' ? Math.sin(t * 2.2) * 0.01 : c.pointerY * 0.012) * (reduced ? 0.2 : 1);
    if (pupilL.current) pupilL.current.position.set(-0.004 + lookX, lookY, 0.028);
    if (pupilR.current) pupilR.current.position.set(0.004 + lookX, lookY, 0.028);

    if (scanRef.current) {
      const show = st === 'thinking' ? 0.95 : 0;
      scanRef.current.material.opacity = damp(scanRef.current.material.opacity, show, 8, step);
      scanRef.current.position.x = Math.sin(t * 3.4) * 0.07 * motion;
    }

    if (mouthRef.current) {
      const smile = st === 'success' ? 1 : st === 'error' ? -0.85 : st === 'speaking' ? 0.25 : 0.08;
      const open = st === 'speaking'
        ? (level == null ? 0.55 : 0.15 + level * 1.15)
        : st === 'listening' && level != null
          ? level * 0.25
          : 0.08;
      const kids = mouthRef.current.children;
      for (let i = 0; i < kids.length; i += 1) {
        const u = kids.length === 1 ? 0.5 : i / (kids.length - 1);
        const arc = Math.sin(u * Math.PI);
        const x = (u - 0.5) * 0.2;
        const y = -0.07 + smile * 0.045 * (arc - 0.2) + (st === 'speaking' ? open * arc * 0.02 : 0);
        kids[i].position.set(x, y, 0.02);
        kids[i].scale.set(1, 0.55 + open * (st === 'speaking' ? 1.8 : 0.2), 1);
      }
    }

    if (antennaRef.current) {
      const listenBoost = st === 'listening' ? 1.8 + (level == null ? 0.4 : level * 1.4) : 0;
      const thinkPulse = st === 'thinking' ? 0.7 + 0.6 * Math.sin(t * 7) : 0;
      const base = 0.85 + (reduced ? 0 : 0.15 * Math.sin(t * 1.5));
      antennaRef.current.scale.setScalar(damp(antennaRef.current.scale.x, base + listenBoost * 0.18 + (st === 'success' ? 0.25 : 0), 8, step));
      antennaRef.current.material.color.lerp(LED[st] || LED.idle, k);
      const extra = listenBoost + thinkPulse + (st === 'success' ? 0.8 : 0);
      antennaRef.current.material.opacity = 1;
      antennaRef.current.userData.glow = extra;
    }

    if (chestRef.current) {
      const work = st === 'working' ? 0.55 + 0.45 * Math.sin(t * 8) : st === 'error' ? 0.45 + 0.25 * Math.sin(t * 3.4) : 0.25 + 0.08 * Math.sin(t * 2);
      chestRef.current.scale.setScalar(0.85 + (reduced ? 0.1 : work * 0.45));
    }

    if (earL.current) earL.current.rotation.z += step * (st === 'working' || st === 'thinking' ? 1.6 : 0.45) * (reduced ? 0.15 : 1);
    if (earR.current) earR.current.rotation.z -= step * (st === 'working' || st === 'thinking' ? 1.6 : 0.45) * (reduced ? 0.15 : 1);

    const poseArm = (shoulder, elbow, side, lead) => {
      if (!shoulder.current || !elbow.current) return;
      let sx = 0.25;
      let sz = side * 0.18;
      let ex = 0.35;
      if (st === 'success' && lead) {
        sx = -2.55;
        sz = side * 0.35;
        ex = 0.4 + Math.sin(t * 9) * 0.45 * motion;
      } else if (st === 'speaking' && lead) {
        sx = 0.15 + Math.sin(t * 1.7) * 0.25 * motion;
        ex = 0.45;
      } else if (st === 'working') {
        sx = 0.25 + Math.sin(t * 2.2 + side) * 0.18 * motion;
      } else if (st === 'error') {
        sx = 0.45;
        sz = side * 0.08;
      }
      shoulder.current.rotation.x = damp(shoulder.current.rotation.x, sx, 6, step);
      shoulder.current.rotation.z = damp(shoulder.current.rotation.z, sz, 6, step);
      elbow.current.rotation.x = damp(elbow.current.rotation.x, ex, 8, step);
    };
    poseArm(shoulderL, elbowL, -1, false);
    poseArm(shoulderR, elbowR, 1, true);
  });

  return (
    <group ref={floatRef}>
      <pointLight ref={underLight} position={[0, -0.55, 0.15]} intensity={3.2} distance={3.5} color="#3aa0ff" />

      <mesh ref={glowRef} position={[0, -0.78, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[0.72, 32]} />
        <meshBasicMaterial map={glowMap} transparent depthWrite={false} toneMapped={false} opacity={0.3} />
      </mesh>

      <group ref={ringSpin} position={[0, -0.7, 0]}>
        <group rotation={[0.55, 0, 0.15]}>
          <mesh material={ringMat}>
            <torusGeometry args={[0.5, 0.016, 8, 64]} />
          </mesh>
          <mesh material={rimMat} rotation={[0, 0.4, 0]}>
            <torusGeometry args={[0.36, 0.008, 6, 48]} />
          </mesh>
        </group>
      </group>

      <mesh material={ceramic} position={[0, -0.02, 0]} scale={[0.92, 0.78, 0.78]}>
        <sphereGeometry args={[0.34, 28, 20]} />
      </mesh>
      <mesh material={glass} position={[0, 0.02, 0.16]} scale={[0.7, 0.42, 0.28]}>
        <sphereGeometry args={[0.16, 20, 14]} />
      </mesh>
      <mesh ref={chestRef} position={[0, 0.02, 0.25]} material={ledMat}>
        <sphereGeometry args={[0.035, 12, 10]} />
      </mesh>

      <Arm side={-1} ceramic={ceramic} shoulder={shoulderL} elbow={elbowL} />
      <Arm side={1} ceramic={ceramic} shoulder={shoulderR} elbow={elbowR} />

      <group ref={headRef} position={[0, 0.5, 0]}>
        <mesh material={ceramic} scale={[1.08, 0.98, 0.96]}>
          <sphereGeometry args={[0.4, 32, 24]} />
        </mesh>
        <mesh material={glass} position={[0, -0.02, 0.18]} scale={[1.2, 0.96, 0.55]}>
          <sphereGeometry args={[0.26, 28, 18]} />
        </mesh>
        <mesh material={rimMat} position={[0, -0.02, 0.2]} rotation={[0.15, 0, 0]}>
          <torusGeometry args={[0.27, 0.012, 8, 36]} />
        </mesh>
        <mesh position={[-0.12, 0.12, 0.38]}>
          <sphereGeometry args={[0.02, 8, 8]} />
          <meshBasicMaterial color="#ffffff" transparent opacity={0.8} toneMapped={false} />
        </mesh>

        <group position={[0, 0.02, 0.3]}>
          <group ref={eyeL} position={[-0.095, 0.045, 0]}>
            <mesh material={ledMat}>
              <sphereGeometry args={[0.048, 16, 12]} />
            </mesh>
            <mesh ref={pupilL} position={[0, 0, 0.028]}>
              <sphereGeometry args={[0.02, 10, 8]} />
              <meshBasicMaterial color="#041018" />
            </mesh>
          </group>
          <group ref={eyeR} position={[0.095, 0.045, 0]}>
            <mesh material={ledMat}>
              <sphereGeometry args={[0.048, 16, 12]} />
            </mesh>
            <mesh ref={pupilR} position={[0, 0, 0.028]}>
              <sphereGeometry args={[0.02, 10, 8]} />
              <meshBasicMaterial color="#041018" />
            </mesh>
          </group>
          <mesh ref={scanRef} position={[0, 0.045, 0.04]}>
            <boxGeometry args={[0.18, 0.012, 0.01]} />
            <meshBasicMaterial color="#dff8ff" transparent opacity={0} toneMapped={false} depthWrite={false} />
          </mesh>
          <group ref={mouthRef} position={[0, -0.02, 0.02]}>
            {[0, 1, 2, 3, 4, 5, 6].map((i) => (
              <mesh key={i} material={ledMat}>
                <sphereGeometry args={[0.014, 8, 8]} />
              </mesh>
            ))}
          </group>
        </group>

        <mesh ref={earL} position={[-0.4, 0.04, 0]} rotation={[0.2, 0.5, 0]} material={ledMat}>
          <torusGeometry args={[0.085, 0.022, 10, 22]} />
        </mesh>
        <mesh ref={earR} position={[0.4, 0.04, 0]} rotation={[0.2, -0.5, 0]} material={ledMat}>
          <torusGeometry args={[0.085, 0.022, 10, 22]} />
        </mesh>

        <group position={[0.08, 0.4, 0]} rotation={[0, 0, -0.2]}>
          <mesh material={ceramic} position={[0, 0.1, 0]}>
            <cylinderGeometry args={[0.012, 0.014, 0.2, 8]} />
          </mesh>
          <mesh ref={antennaRef} position={[0, 0.22, 0]} material={ledMat}>
            <sphereGeometry args={[0.038, 12, 12]} />
          </mesh>
        </group>
      </group>
    </group>
  );
}

function Arm({ side, ceramic, shoulder, elbow }) {
  return (
    <group ref={shoulder} position={[side * 0.34, 0.08, 0]}>
      <mesh material={ceramic} position={[side * 0.02, -0.16, 0]}>
        <capsuleGeometry args={[0.045, 0.14, 4, 8]} />
      </mesh>
      <group ref={elbow} position={[side * 0.03, -0.32, 0]}>
        <mesh material={ceramic} position={[0, -0.12, 0]}>
          <capsuleGeometry args={[0.038, 0.1, 4, 8]} />
        </mesh>
        <mesh material={ceramic} position={[side * 0.02, -0.26, 0.02]}>
          <sphereGeometry args={[0.055, 12, 10]} />
        </mesh>
        <mesh material={ceramic} position={[side * 0.055, -0.24, 0.04]}>
          <sphereGeometry args={[0.022, 8, 8]} />
        </mesh>
      </group>
    </group>
  );
}

export function RobotScene({ paused }) {
  return (
    <Canvas
      dpr={[1, 1.5]}
      frameloop={paused ? 'demand' : 'always'}
      gl={{
        alpha: true,
        antialias: true,
        powerPreference: 'high-performance',
        premultipliedAlpha: false,
      }}
      camera={{ position: [0.22, 0.18, 2.72], fov: 34, near: 0.1, far: 20 }}
      style={{ background: 'transparent', pointerEvents: 'none' }}
      onCreated={({ gl }) => {
        gl.setClearColor(0x000000, 0);
      }}
    >
      <FrameDriver />
      <Look />
      <ambientLight intensity={0.55} />
      <directionalLight position={[2.4, 3.6, 2.8]} intensity={2.4} color="#fffaf5" />
      <directionalLight position={[-2.6, 1.2, -1.4]} intensity={1.15} color="#9ecbff" />
      <directionalLight position={[0.4, -1.2, 2]} intensity={0.35} color="#d7e8ff" />
      <React.Suspense fallback={null}>
        <Environment resolution={64}>
          <Lightformer intensity={2.4} position={[0, 2, 2]} scale={[4, 2, 1]} color="#ffffff" />
          <Lightformer intensity={3.2} position={[-2, 1, 1]} scale={[2, 2, 1]} color="#8ec8ff" />
          <Lightformer intensity={1.4} position={[2.2, 0.2, -1]} scale={[2, 3, 1]} color="#e7f2ff" />
        </Environment>
      </React.Suspense>
      <RobotRig />
    </Canvas>
  );
}
