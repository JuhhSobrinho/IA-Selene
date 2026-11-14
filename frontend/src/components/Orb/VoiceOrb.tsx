import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { useAppStore } from '../../lib/store';
import { useTtsStore } from '../../lib/tts';
import { useSpeechStore } from '../../hooks/useSpeech';

type OrbPhase = 'idle' | 'listening' | 'thinking' | 'speaking';

// One hue family (green), like the reference looks — phases shift
// brightness/warmth rather than jumping to an unrelated color.
const PHASE_COLOR: Record<OrbPhase, THREE.Color> = {
  idle: new THREE.Color(0x22c55e), // steady green
  listening: new THREE.Color(0x84ec3d), // brighter yellow-green — "hearing you"
  thinking: new THREE.Color(0x10aa82), // cooler teal-green — "processing"
  speaking: new THREE.Color(0x4ade80), // bright emerald, pulsing
};

const PHASE_LABEL: Record<OrbPhase, string> = {
  idle: '',
  listening: 'Ouvindo…',
  thinking: 'Pensando…',
  speaking: 'Falando…',
};

// Ashima Arts' classic 3D simplex noise (public-domain-licensed, widely
// reused — see github.com/ashima/webgl-noise). Self-contained so the
// "thought cloud" turbulence needs no noise texture.
const SIMPLEX_NOISE_GLSL = `
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
  const vec2 C = vec2(1.0/6.0, 1.0/3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);

  vec3 i  = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);

  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);

  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;

  i = mod289(i);
  vec4 p = permute(permute(permute(
            i.z + vec4(0.0, i1.z, i2.z, 1.0))
          + i.y + vec4(0.0, i1.y, i2.y, 1.0))
          + i.x + vec4(0.0, i1.x, i2.x, 1.0));

  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;

  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);

  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);

  vec4 x = x_ *ns.x + ns.yyyy;
  vec4 y = y_ *ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);

  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);

  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));

  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;

  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);

  vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;

  vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
}
`;

const VERTEX_SHADER = `
attribute float aSeed;
attribute float aDriftAmp;
attribute float aDriftSpeed;
uniform float uTime;
uniform float uNoiseAmp;
uniform float uEnvelope;
uniform float uPixelRatio;
uniform float uBaseSize;
uniform float uTurbulence;
${SIMPLEX_NOISE_GLSL}
varying float vDepth;

void main() {
  // Each particle has only a small, fast shimmer of its own (feathers
  // ruffling) — the group as a whole stays a cohesive round flock and
  // migrates through the room together (see points.position.* in the
  // component), rather than individual particles scattering off alone
  // like fireflies.
  float phase = aSeed * 6.2831853;
  float speed = aDriftSpeed * uTime;
  vec3 roam = vec3(
    sin(speed + phase) * aDriftAmp,
    cos(speed * 0.8 + phase * 1.3) * aDriftAmp,
    sin(speed * 0.6 + phase * 0.7) * aDriftAmp * 0.6
  );

  // Gather tighter / spread looser with uEnvelope — this is the main
  // "thinking" tell now (not the group drifting across the room): a
  // focused, denser cluster while engaged, relaxed and looser at idle.
  float gather = 1.0 - uEnvelope * 0.32;
  vec3 pos = position * gather + roam;

  // Murmuration-style "unraveling": a slow, spatially-coherent flow field
  // (sampled from world position, not per-particle seed) so neighbouring
  // particles get near-identical offsets and the WHOLE flock bends/stretches
  // into a tendril together, like a starling cloud, then relaxes back into
  // the round shape when uTurbulence subsides. Per-particle seed noise below
  // stays separate and small (shimmer), not the thing driving this shape.
  vec3 warpField = pos * 0.7 + vec3(0.0, uTime * 0.12, uTime * 0.05);
  vec3 warp = vec3(
    snoise(warpField + vec3(11.3, 0.0, 0.0)),
    snoise(warpField + vec3(0.0, 47.7, 0.0)),
    snoise(warpField + vec3(0.0, 0.0, 29.1))
  );
  pos += warp * uTurbulence * 1.1;

  float t = uTime * 0.25 + aSeed * 10.0;
  vec3 noiseCoord = pos * 0.5 + vec3(t);
  float n1 = snoise(noiseCoord);
  float n2 = snoise(noiseCoord + vec3(5.2, 1.3, 7.1));
  float n3 = snoise(noiseCoord + vec3(2.7, 9.1, 3.4));
  vec3 displacement = vec3(n1, n2, n3) * (uNoiseAmp * (0.6 + 0.4 * uEnvelope));
  pos += displacement;

  vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
  vDepth = -mvPosition.z;
  gl_Position = projectionMatrix * mvPosition;
  // uBaseSize is the desired on-screen point diameter (px) at the camera's
  // reference distance (4.2, matching camera.position.z in the component).
  gl_PointSize = uBaseSize * uPixelRatio * (4.2 / -mvPosition.z) * (0.7 + 0.5 * aSeed);
}
`;

const FRAGMENT_SHADER = `
uniform vec3 uColor;
uniform float uOpacity;
varying float vDepth;

void main() {
  vec2 uv = gl_PointCoord - vec2(0.5);
  float d = length(uv);
  if (d > 0.5) discard;
  float glow = smoothstep(0.5, 0.0, d);
  float core = smoothstep(0.18, 0.0, d);
  float depthFade = clamp(1.6 - vDepth * 0.12, 0.25, 1.0);
  vec3 col = uColor + core * 0.6;
  gl_FragColor = vec4(col, (glow * 0.8 + core * 0.4) * uOpacity * depthFade);
}
`;

const PARTICLE_COUNT = 4500;

function buildGeometry(count: number): THREE.BufferGeometry {
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count);
  const driftAmps = new Float32Array(count);
  const driftSpeeds = new Float32Array(count);

  for (let i = 0; i < count; i++) {
    // Uniform-density scatter through a roughly spherical volume (cube-root
    // radius), not just a shell surface — gives real depth to roam through,
    // like a flock with birds at every distance, not a hollow ball.
    const u = Math.random();
    const radius = Math.cbrt(u) * 1.05;
    const theta = Math.random() * Math.PI * 2;
    const costheta = Math.random() * 2 - 1;
    const sintheta = Math.sqrt(1 - costheta * costheta);

    positions[i * 3] = Math.cos(theta) * sintheta * radius;
    positions[i * 3 + 1] = costheta * radius;
    positions[i * 3 + 2] = Math.sin(theta) * sintheta * radius;

    seeds[i] = Math.random();
    driftAmps[i] = 0.03 + Math.random() * 0.09;
    driftSpeeds[i] = 0.4 + Math.random() * 0.6;
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 1));
  geometry.setAttribute('aDriftAmp', new THREE.BufferAttribute(driftAmps, 1));
  geometry.setAttribute('aDriftSpeed', new THREE.BufferAttribute(driftSpeeds, 1));
  return geometry;
}

export interface VoiceOrbProps {
  /** Fallback pixel size while the container hasn't been measured yet. */
  size?: number;
  className?: string;
}

export function VoiceOrb({ size = 180, className }: VoiceOrbProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [debugError, setDebugError] = useState<string | null>(null);

  const isStreaming = useAppStore((s) => s.streamState.isStreaming);
  const ttsState = useTtsStore((s) => s.state);
  const micState = useSpeechStore((s) => s.state);
  const micLevel = useSpeechStore((s) => s.level);

  // Read the latest reactive values inside the render loop via a ref, so
  // the WebGL scene is built once and never torn down on a state change.
  const liveRef = useRef({ isStreaming, ttsState, micState, micLevel });
  liveRef.current = { isStreaming, ttsState, micState, micLevel };

  useEffect(() => {
    const wrapper = wrapperRef.current;
    const container = containerRef.current;
    if (!wrapper || !container) return;

    let renderer: THREE.WebGLRenderer;
    try {
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setClearColor(0x000000, 0);
      renderer.setPixelRatio(pixelRatio);
      container.appendChild(renderer.domElement);
    } catch (err) {
      console.error('[VoiceOrb] renderer creation failed', err);
      setDebugError(`renderer: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }

    try {
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
      camera.position.z = 4.2;

      const geometry = buildGeometry(PARTICLE_COUNT);
      const material = new THREE.ShaderMaterial({
        vertexShader: VERTEX_SHADER,
        fragmentShader: FRAGMENT_SHADER,
        uniforms: {
          uTime: { value: 0 },
          uNoiseAmp: { value: 0.2 },
          uEnvelope: { value: 0 },
          uPixelRatio: { value: pixelRatio },
          uBaseSize: { value: 3.2 },
          uTurbulence: { value: 0 },
          uColor: { value: new THREE.Color(PHASE_COLOR.idle) },
          uOpacity: { value: 0.85 },
        },
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });

      const points = new THREE.Points(geometry, material);
      scene.add(points);

      // Fill the parent — the "canvas" is the whole available area, not a
      // fixed square, so the flock has real room to migrate through
      // (especially once the side chat panel is open and the right column
      // widens). The flock's own shape stays compact (see buildGeometry);
      // only its group position roams, tracked in world units derived from
      // the actual visible frustum so the range scales with the container.
      let visibleHalfWidth = 1;
      let visibleHalfHeight = 1;
      const resize = () => {
        const w = wrapper.clientWidth || size;
        const h = wrapper.clientHeight || size;
        renderer.setSize(w, h);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        const vFov = (camera.fov * Math.PI) / 180;
        visibleHalfHeight = Math.tan(vFov / 2) * camera.position.z;
        visibleHalfWidth = visibleHalfHeight * camera.aspect;
      };
      resize();
      const resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(wrapper);

      const currentColor = new THREE.Color(PHASE_COLOR.idle);
      let currentEnvelope = 0;
      let speakEnvelope = 0;
      let currentTurbulence = 0;
      let rotY = 0;

      let raf = 0;
      const start = performance.now();

      const render = (now: number) => {
        const t = (now - start) / 1000;
        const { isStreaming: thinking, ttsState: tts, micState: mic, micLevel: level } =
          liveRef.current;

        const phase: OrbPhase =
          mic === 'recording'
            ? 'listening'
            : mic === 'transcribing' || thinking
              ? 'thinking'
              : tts === 'speaking' || tts === 'loading'
                ? 'speaking'
                : 'idle';

        let targetEnvelope: number;
        if (phase === 'listening') {
          targetEnvelope = 0.2 + level * 0.8;
        } else if (phase === 'speaking') {
          speakEnvelope =
            0.3 +
            0.55 *
              Math.abs(Math.sin(t * 9) * 0.6 + Math.sin(t * 13 + 1.3) * 0.4);
          targetEnvelope = speakEnvelope;
        } else if (phase === 'thinking') {
          targetEnvelope = 0.32 + 0.1 * Math.sin(t * 4);
        } else {
          targetEnvelope = 0.1 + 0.05 * Math.sin(t * 1.2);
        }

        // Smooth transitions so phase changes don't snap the flock.
        currentEnvelope += (targetEnvelope - currentEnvelope) * 0.08;
        currentColor.lerp(PHASE_COLOR[phase], 0.06);

        // Irregular "unravel" bursts (the murmuration reference): three
        // incommensurate sine frequencies summed read like organic noise
        // rather than a metronome, then only the positive peaks survive and
        // get cubed so the flock spends most of its time as a calm, tight
        // circle and only occasionally, briefly stretches into a tendril.
        const turbRaw =
          (Math.sin(t * 0.17) + Math.sin(t * 0.193) + Math.sin(t * 0.077 + 1.7)) / 3;
        const targetTurbulence = Math.pow(Math.max(0, turbRaw), 3) * 0.9;
        currentTurbulence += (targetTurbulence - currentTurbulence) * 0.04;

        // The flock stays anchored at the center — a cohesive round shape
        // (see VERTEX_SHADER / buildGeometry) with only a small sway, not a
        // trip across the room. What changes with the AI's state is how
        // tightly it gathers vs. spreads (uEnvelope, in the shader) and a
        // gentle bank/turn, not where it is.
        const spinSpeed =
          phase === 'thinking' ? 0.18 : phase === 'listening' ? 0.12 : 0.06;
        rotY += spinSpeed * 0.016;
        points.rotation.y = rotY;
        points.rotation.x = Math.sin(t * 0.1) * 0.15;
        points.position.x =
          (Math.sin(t * 0.17) * 0.65 + Math.sin(t * 0.07 + 1.1) * 0.35) *
          visibleHalfWidth *
          0.08;
        points.position.y =
          (Math.cos(t * 0.13) * 0.6 + Math.sin(t * 0.09 + 2.3) * 0.4) *
          visibleHalfHeight *
          0.06;

        material.uniforms.uTime.value = t;
        material.uniforms.uEnvelope.value = currentEnvelope;
        material.uniforms.uTurbulence.value = currentTurbulence;
        material.uniforms.uColor.value = currentColor;

        renderer.render(scene, camera);
        raf = requestAnimationFrame(render);
      };

      raf = requestAnimationFrame(render);

      return () => {
        resizeObserver.disconnect();
        cancelAnimationFrame(raf);
        geometry.dispose();
        material.dispose();
        renderer.dispose();
        if (renderer.domElement.parentNode === container) {
          container.removeChild(renderer.domElement);
        }
      };
    } catch (err) {
      console.error('[VoiceOrb] scene setup failed', err);
      setDebugError(`scene: ${err instanceof Error ? err.message : String(err)}`);
      renderer.dispose();
      if (renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
      return;
    }
    // Built once per mount — state is read live via liveRef; sizing is
    // handled by the internal ResizeObserver, not a prop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      ref={wrapperRef}
      className={className}
      style={{ width: '100%', height: '100%', minWidth: size, minHeight: size, position: 'relative' }}
    >
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} aria-hidden="true" />
      {debugError && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 8,
            fontSize: 10,
            color: 'red',
            textAlign: 'center',
            overflow: 'auto',
            background: 'rgba(0,0,0,0.05)',
          }}
        >
          {debugError}
        </div>
      )}
    </div>
  );
}

/** Small text readout of the orb's current phase (mic/thinking/speaking). */
export function VoiceOrbStatus({ className }: { className?: string }) {
  const isStreaming = useAppStore((s) => s.streamState.isStreaming);
  const ttsState = useTtsStore((s) => s.state);
  const micState = useSpeechStore((s) => s.state);

  const phase: OrbPhase =
    micState === 'recording'
      ? 'listening'
      : micState === 'transcribing' || isStreaming
        ? 'thinking'
        : ttsState === 'speaking' || ttsState === 'loading'
          ? 'speaking'
          : 'idle';

  const label = PHASE_LABEL[phase];
  if (!label) return null;

  const color = PHASE_COLOR[phase];
  return (
    <div
      className={className}
      style={{
        fontSize: 12,
        fontWeight: 500,
        color: `#${color.getHexString()}`,
        textAlign: 'center',
      }}
    >
      {label}
    </div>
  );
}
