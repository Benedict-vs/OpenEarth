/**
 * A MapLibre custom layer rendering advecting wind particles on the GPU.
 *
 * Technique vendored from mapbox/webgl-wind (ISC) — particle positions live in an
 * RGBA state texture, advanced each frame by an update pass that samples a wind
 * texture (built from /wind/field). The draw pass projects each particle through
 * web-mercator + the MapLibre matrix so the field is pinned to geography. RTT runs
 * in `prerender`; the geographic point draw runs in `render`.
 */
import type {
  CustomLayerInterface,
  CustomRenderMethodInput,
  Map as MapLibreMap,
} from "maplibre-gl";
import {
  bindAttribute,
  bindFramebuffer,
  bindTexture,
  createBuffer,
  createProgram,
  createTexture,
  type GL,
  type GlProgram,
} from "./glUtil";
import { DRAW_FRAG, DRAW_VERT, QUAD_VERT, UPDATE_FRAG } from "./shaders";
import type { WindTexture } from "./windTexture";

// Speed ramp (slow → fast): calm blues into a warm, bright fast end.
const RAMP_STOPS: [number, string][] = [
  [0.0, "#3288bd"],
  [0.2, "#66c2a5"],
  [0.4, "#abdda4"],
  [0.55, "#e6f598"],
  [0.7, "#fee08b"],
  [0.85, "#fc8d59"],
  [1.0, "#f0f0f0"],
];

function colorRampData(): Uint8Array {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 1;
  const ctx = canvas.getContext("2d")!;
  const gradient = ctx.createLinearGradient(0, 0, 256, 0);
  for (const [stop, color] of RAMP_STOPS) gradient.addColorStop(stop, color);
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 256, 1);
  return new Uint8Array(ctx.getImageData(0, 0, 256, 1).data); // 256*4 → a 16×16 tex
}

export class WindParticleLayer implements CustomLayerInterface {
  readonly id = "wind-particles";
  readonly type = "custom" as const;
  readonly renderingMode = "2d" as const;

  private gl: GL | null = null;
  private map: MapLibreMap | null = null;
  private drawProgram: GlProgram | null = null;
  private updateProgram: GlProgram | null = null;
  private quadBuffer: WebGLBuffer | null = null;
  private framebuffer: WebGLFramebuffer | null = null;
  private colorRampTexture: WebGLTexture | null = null;

  private windTex: WebGLTexture | null = null;
  private wind: WindTexture | null = null;
  private bbox: [number, number, number, number] = [0, 0, 0, 0];

  private particleStateTex0: WebGLTexture | null = null;
  private particleStateTex1: WebGLTexture | null = null;
  private particleIndexBuffer: WebGLBuffer | null = null;
  private particleStateRes = 0;
  private numParticles = 0;

  // Tunables.
  private wantParticles = 4096;
  speedFactor = 45;
  dropRate = 0.003;
  dropRateBump = 0.01;
  streakFraction = 0.05; // streak length as a fraction of the view height
  opacity = 1.0;

  constructor(numParticles = 4096) {
    this.wantParticles = numParticles;
  }

  onAdd(map: MapLibreMap, gl: GL): void {
    this.map = map;
    this.gl = gl;
    this.drawProgram = createProgram(gl, DRAW_VERT, DRAW_FRAG);
    this.updateProgram = createProgram(gl, QUAD_VERT, UPDATE_FRAG);
    this.quadBuffer = createBuffer(gl, new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]));
    this.framebuffer = gl.createFramebuffer();
    this.colorRampTexture = createTexture(gl, gl.LINEAR, colorRampData(), 16, 16);
    this.initParticles(this.wantParticles);
  }

  onRemove(): void {
    const gl = this.gl;
    if (!gl) return;
    for (const t of [
      this.windTex,
      this.colorRampTexture,
      this.particleStateTex0,
      this.particleStateTex1,
    ]) {
      if (t) gl.deleteTexture(t);
    }
    for (const b of [this.quadBuffer, this.particleIndexBuffer]) if (b) gl.deleteBuffer(b);
    if (this.framebuffer) gl.deleteFramebuffer(this.framebuffer);
    if (this.drawProgram) gl.deleteProgram(this.drawProgram.program);
    if (this.updateProgram) gl.deleteProgram(this.updateProgram.program);
    this.gl = null;
    this.map = null;
  }

  /** (Re)allocate the particle state textures for ~*count* particles. */
  setNumParticles(count: number): void {
    if (this.gl) this.initParticles(count);
  }

  private initParticles(count: number): void {
    const gl = this.gl!;
    const res = (this.particleStateRes = Math.ceil(Math.sqrt(count)));
    this.numParticles = res * res;
    const state = new Uint8Array(this.numParticles * 4);
    for (let i = 0; i < state.length; i++) state[i] = Math.floor(Math.random() * 256);
    if (this.particleStateTex0) gl.deleteTexture(this.particleStateTex0);
    if (this.particleStateTex1) gl.deleteTexture(this.particleStateTex1);
    this.particleStateTex0 = createTexture(gl, gl.NEAREST, state, res, res);
    this.particleStateTex1 = createTexture(gl, gl.NEAREST, state, res, res);
    // Two vertices per particle (streak base + tip): a_vertex = 0 … 2N-1.
    const indices = new Float32Array(this.numParticles * 2);
    for (let i = 0; i < indices.length; i++) indices[i] = i;
    if (this.particleIndexBuffer) gl.deleteBuffer(this.particleIndexBuffer);
    this.particleIndexBuffer = createBuffer(gl, indices);
  }

  /** Upload a new wind field (rebuild the wind texture). */
  setWind(tex: WindTexture, bbox: [number, number, number, number]): void {
    const gl = this.gl;
    this.wind = tex;
    this.bbox = bbox;
    if (!gl) return;
    if (this.windTex) gl.deleteTexture(this.windTex);
    this.windTex = createTexture(gl, gl.LINEAR, tex.data, tex.width, tex.height);
  }

  prerender(gl: GL): void {
    if (!this.wind || !this.windTex) return;
    this.updateParticles(gl);
  }

  render(gl: GL, options: CustomRenderMethodInput): void {
    if (!this.wind || !this.windTex) return;
    this.drawParticles(gl, Array.from(options.defaultProjectionData.mainMatrix));
    this.map?.triggerRepaint();
  }

  private updateParticles(gl: GL): void {
    const prog = this.updateProgram!;
    const res = this.particleStateRes;
    bindFramebuffer(gl, this.framebuffer, this.particleStateTex1!);
    gl.viewport(0, 0, res, res);
    gl.disable(gl.BLEND);

    gl.useProgram(prog.program);
    bindAttribute(gl, this.quadBuffer!, prog.a_pos as number, 2);
    bindTexture(gl, this.windTex!, 0);
    bindTexture(gl, this.particleStateTex0!, 1);
    gl.uniform1i(prog.u_wind as WebGLUniformLocation, 0);
    gl.uniform1i(prog.u_particles as WebGLUniformLocation, 1);
    gl.uniform2f(prog.u_wind_res as WebGLUniformLocation, this.wind!.width, this.wind!.height);
    gl.uniform2f(
      prog.u_wind_min as WebGLUniformLocation,
      this.wind!.windMin[0],
      this.wind!.windMin[1],
    );
    gl.uniform2f(
      prog.u_wind_max as WebGLUniformLocation,
      this.wind!.windMax[0],
      this.wind!.windMax[1],
    );
    gl.uniform1f(prog.u_rand_seed as WebGLUniformLocation, Math.random());
    gl.uniform1f(prog.u_speed_factor as WebGLUniformLocation, this.speedFactor);
    gl.uniform1f(prog.u_drop_rate as WebGLUniformLocation, this.dropRate);
    gl.uniform1f(prog.u_drop_rate_bump as WebGLUniformLocation, this.dropRateBump);
    gl.drawArrays(gl.TRIANGLES, 0, 6);

    // Swap: the freshly written texture becomes the current state.
    [this.particleStateTex0, this.particleStateTex1] = [
      this.particleStateTex1,
      this.particleStateTex0,
    ];
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  private drawParticles(gl: GL, matrix: number[]): void {
    const prog = this.drawProgram!;
    gl.useProgram(prog.program);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    bindAttribute(gl, this.particleIndexBuffer!, prog.a_vertex as number, 1);
    bindTexture(gl, this.particleStateTex0!, 1);
    bindTexture(gl, this.windTex!, 0);
    bindTexture(gl, this.colorRampTexture!, 2);
    gl.uniform1i(prog.u_particles as WebGLUniformLocation, 1);
    gl.uniform1i(prog.u_wind as WebGLUniformLocation, 0);
    gl.uniform1i(prog.u_color_ramp as WebGLUniformLocation, 2);
    gl.uniform1f(prog.u_particles_res as WebGLUniformLocation, this.particleStateRes);
    gl.uniformMatrix4fv(prog.u_matrix as WebGLUniformLocation, false, matrix);
    gl.uniform4f(
      prog.u_bbox as WebGLUniformLocation,
      this.bbox[0],
      this.bbox[1],
      this.bbox[2],
      this.bbox[3],
    );
    const streakDeg = (this.bbox[3] - this.bbox[1]) * this.streakFraction;
    gl.uniform1f(prog.u_streak_deg as WebGLUniformLocation, streakDeg);
    gl.uniform2f(
      prog.u_wind_min as WebGLUniformLocation,
      this.wind!.windMin[0],
      this.wind!.windMin[1],
    );
    gl.uniform2f(
      prog.u_wind_max as WebGLUniformLocation,
      this.wind!.windMax[0],
      this.wind!.windMax[1],
    );
    gl.uniform1f(prog.u_opacity as WebGLUniformLocation, this.opacity);
    gl.drawArrays(gl.LINES, 0, this.numParticles * 2);
  }
}
