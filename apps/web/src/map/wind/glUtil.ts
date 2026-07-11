/** Minimal WebGL helpers for the wind particle layer (adapted from mapbox/webgl-wind, ISC). */

export type GL = WebGLRenderingContext | WebGL2RenderingContext;

export interface GlProgram {
  program: WebGLProgram;
  [name: string]: WebGLProgram | number | WebGLUniformLocation | null;
}

function compile(gl: GL, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)!;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader) ?? "shader compile failed");
  }
  return shader;
}

/** Compile + link a program and expose its attribute/uniform locations by name. */
export function createProgram(gl: GL, vertex: string, fragment: string): GlProgram {
  const program = gl.createProgram()!;
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vertex));
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, fragment));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) ?? "program link failed");
  }
  const wrapper: GlProgram = { program };
  const nAttr = gl.getProgramParameter(program, gl.ACTIVE_ATTRIBUTES) as number;
  for (let i = 0; i < nAttr; i++) {
    const a = gl.getActiveAttrib(program, i)!;
    wrapper[a.name] = gl.getAttribLocation(program, a.name);
  }
  const nUnif = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS) as number;
  for (let i = 0; i < nUnif; i++) {
    const u = gl.getActiveUniform(program, i)!;
    wrapper[u.name] = gl.getUniformLocation(program, u.name);
  }
  return wrapper;
}

export function createTexture(
  gl: GL,
  filter: number,
  data: Uint8Array,
  width: number,
  height: number,
): WebGLTexture {
  const texture = gl.createTexture()!;
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
  gl.bindTexture(gl.TEXTURE_2D, null);
  return texture;
}

export function bindTexture(gl: GL, texture: WebGLTexture, unit: number): void {
  gl.activeTexture(gl.TEXTURE0 + unit);
  gl.bindTexture(gl.TEXTURE_2D, texture);
}

export function createBuffer(gl: GL, data: Float32Array): WebGLBuffer {
  const buffer = gl.createBuffer()!;
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  return buffer;
}

export function bindAttribute(
  gl: GL,
  buffer: WebGLBuffer,
  attribute: number,
  numComponents: number,
): void {
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.enableVertexAttribArray(attribute);
  gl.vertexAttribPointer(attribute, numComponents, gl.FLOAT, false, 0, 0);
}

export function bindFramebuffer(
  gl: GL,
  framebuffer: WebGLFramebuffer | null,
  texture?: WebGLTexture,
): void {
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
  if (texture) {
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
  }
}
