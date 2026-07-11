/**
 * GLSL for the wind particle layer.
 *
 * Adapted from mapbox/webgl-wind (Vladimir Agafonkin, ISC License) —
 * https://github.com/mapbox/webgl-wind. Particle state lives in an RGBA texture
 * (position encoded across R/B and G/A); an update pass advects each particle by
 * the bilinearly-sampled wind and randomly respawns it. The draw pass differs from
 * the original: instead of a full-screen quad it projects each particle's
 * [0,1]-in-bbox position through web-mercator and the MapLibre projection matrix so
 * the field stays pinned to geography under pan/zoom.
 */

// Fullscreen quad for the update (render-to-texture) pass.
export const QUAD_VERT = `
precision mediump float;
attribute vec2 a_pos;
varying vec2 v_tex_pos;
void main() {
  v_tex_pos = a_pos;
  gl_Position = vec4(1.0 - 2.0 * a_pos, 0.0, 1.0);
}`;

// Advect + respawn particles; write the new positions back to the state texture.
export const UPDATE_FRAG = `
precision highp float;
uniform sampler2D u_particles;
uniform sampler2D u_wind;
uniform vec2 u_wind_res;
uniform vec2 u_wind_min;
uniform vec2 u_wind_max;
uniform float u_rand_seed;
uniform float u_speed_factor;
uniform float u_drop_rate;
uniform float u_drop_rate_bump;
varying vec2 v_tex_pos;

const vec3 rand_constants = vec3(12.9898, 78.233, 4375.85453);
float rand(const vec2 co) {
  float t = dot(rand_constants.xy, co);
  return fract(sin(t) * (rand_constants.z + t));
}

vec2 lookup_wind(const vec2 uv) {
  vec2 px = 1.0 / u_wind_res;
  vec2 vc = (floor(uv * u_wind_res)) * px;
  vec2 f = fract(uv * u_wind_res);
  vec2 tl = texture2D(u_wind, vc).rg;
  vec2 tr = texture2D(u_wind, vc + vec2(px.x, 0.0)).rg;
  vec2 bl = texture2D(u_wind, vc + vec2(0.0, px.y)).rg;
  vec2 br = texture2D(u_wind, vc + px).rg;
  return mix(mix(tl, tr, f.x), mix(bl, br, f.x), f.y);
}

void main() {
  vec4 color = texture2D(u_particles, v_tex_pos);
  vec2 pos = vec2(color.r / 255.0 + color.b, color.g / 255.0 + color.a); // 0..1

  vec2 velocity = mix(u_wind_min, u_wind_max, lookup_wind(pos));
  float speed_t = length(velocity) / length(u_wind_max);

  // pos.x east-positive, pos.y south-positive (y=0 is the NW/north edge), so
  // northward wind (+v) decreases y.
  vec2 offset = vec2(velocity.x, -velocity.y) * 0.0001 * u_speed_factor;
  pos = fract(1.0 + pos + offset);

  // randomly reset a fraction of particles each frame to avoid clumping.
  vec2 seed = (pos + v_tex_pos) * u_rand_seed;
  float drop_rate = u_drop_rate + speed_t * u_drop_rate_bump;
  float drop = step(1.0 - drop_rate, rand(seed));
  vec2 random_pos = vec2(rand(seed + 1.3), rand(seed + 2.1));
  pos = mix(pos, random_pos, drop);

  gl_FragColor = vec4(fract(pos * 255.0), floor(pos * 255.0) / 255.0);
}`;

// Each particle is a short streak (2 vertices) oriented along its local wind, so a
// single frame shows direction (matching the arrow overlay); the streaks advect as
// the state texture updates. a_vertex packs particle = floor(a/2), end = mod(a,2).
export const DRAW_VERT = `
precision mediump float;
attribute float a_vertex;
uniform sampler2D u_particles;
uniform sampler2D u_wind;
uniform vec2 u_wind_min;
uniform vec2 u_wind_max;
uniform float u_particles_res;
uniform mat4 u_matrix;
uniform vec4 u_bbox;       // west, south, east, north
uniform float u_streak_deg;
varying float v_speed_t;
varying float v_end;

const float PI = 3.1415926535897932384626433832795;

void main() {
  float particle = floor(a_vertex / 2.0);
  float end = mod(a_vertex, 2.0);
  vec4 color = texture2D(u_particles, vec2(
    fract(particle / u_particles_res),
    floor(particle / u_particles_res) / u_particles_res));
  vec2 pos = vec2(color.r / 255.0 + color.b, color.g / 255.0 + color.a);

  vec2 velocity = mix(u_wind_min, u_wind_max, texture2D(u_wind, pos).rg);
  float speed = length(velocity);
  v_speed_t = clamp(speed / length(u_wind_max), 0.0, 1.0);
  v_end = end;
  vec2 dir = speed > 1e-4 ? velocity / speed : vec2(0.0); // (east, north)

  // Base at the particle; tip offset along the wind direction (east +lon, north +lat).
  float lon = u_bbox.x + pos.x * (u_bbox.z - u_bbox.x) + end * dir.x * u_streak_deg;
  float lat = (u_bbox.w - pos.y * (u_bbox.w - u_bbox.y)) + end * dir.y * u_streak_deg;
  float mx = (lon + 180.0) / 360.0;
  float sinLat = clamp(sin(radians(lat)), -0.9999, 0.9999);
  float my = 0.5 - log((1.0 + sinLat) / (1.0 - sinLat)) / (4.0 * PI);

  gl_Position = u_matrix * vec4(mx, my, 0.0, 1.0);
}`;

// Colour each streak by speed via a 16-stop ramp; brighter at the leading tip.
export const DRAW_FRAG = `
precision mediump float;
uniform sampler2D u_color_ramp;
uniform float u_opacity;
varying float v_speed_t;
varying float v_end;
void main() {
  vec2 ramp_pos = vec2(fract(16.0 * v_speed_t), floor(16.0 * v_speed_t) / 16.0);
  vec4 color = texture2D(u_color_ramp, ramp_pos);
  float alpha = mix(0.45, 1.0, v_end) * u_opacity; // fade toward the tail
  gl_FragColor = vec4(color.rgb, color.a * alpha);
}`;
