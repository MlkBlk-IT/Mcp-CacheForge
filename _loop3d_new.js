/* ==================================================================
   LOOP3D — THE AGENT LOOP
   ------------------------------------------------------------------
   The old donut is gone. What stands here now is a lemniscate of
   Bernoulli — a real ∞ — swept as a solid, lit 3D ribbon.

     shape  : 'loop'  → ∞ lemniscate (default)   'donut' → classic torus
     flight : the camera rides the ribbon through space, banked, with
              the conduit rushing past on both sides
     bolts  : 0 off · 1 normal · 2 storm — manga-grade discharges that
              race *inside* the tube, forked, white-cored, with a
              screen flash and a camera kick
     gauge  : the hit-rate charges the ribbon; the head rides the curve
              and lights up the five stages of the agent pipeline
   ================================================================== */
const LOOP_LIME = [57, 255, 168], LOOP_CYAN = [34, 230, 255],
      LOOP_VIOLET = [197, 107, 255], LOOP_HOT = [255, 92, 150];
const LOOP_TAU = Math.PI * 2;
const LOOP_STAGES = ['PROMPT', 'NORMALIZE', 'KEY', 'CACHE', 'SERVE'];
const mixc = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

class Loop3D {
  constructor(canvas){
    this.cv = canvas; this.ctx = canvas.getContext('2d');
    this.W = canvas.width; this.H = canvas.height;
    this.pct = 0; this.target = 0;
    this.yaw = 0; this.tilt = 0.30; this.drift = 0; this.dt = 0.016;
    this.zoom = 1; this.autoSpin = true; this.manual = false; this.drag = null;
    this.mode = 'loop'; this.flight = false; this.boltLevel = 2; this.thick = 1;
    this.flash = 0; this.shake = 0; this.flightT = 0;
    this.arcCd = 0.7; this.boltCd = 0;
    this.activeStage = 0;
    this.bolts = []; this.arcs = []; this.streaks = []; this.motes = [];
    for (let i = 0; i < 24; i++)
      this.motes.push({ u:Math.random(), a:Math.random()*LOOP_TAU, r:0.32+Math.random()*0.5, s:0.5+Math.random()*1.4, sp:(Math.random()<0.5?-1:1)*(0.05+Math.random()*0.12) });
    for (let i = 0; i < 54; i++)
      this.streaks.push({ a:Math.random()*LOOP_TAU, r:Math.random()*1.2, sp:0.55+Math.random()*1.5, l:0.05+Math.random()*0.14 });
    this.build();
    this.resize();
    this.bindControls();
  }

  /* ==================== geometry ==================== */
  curve(t){
    if (this.mode === 'donut') return [Math.cos(t), 0, Math.sin(t)];
    const c = Math.cos(t), s = Math.sin(t);
    return [c * 1.02, s * c * 1.62, 0];
  }
  /* the ribbon: a tube swept along the curve, with a stable frame per station */
  build(){
    const NU = this.NU = 92, NV = this.NV = 10;
    const ref = this.mode === 'donut' ? [0, 1, 0] : [0, 0, 1];
    const C = [], T = [];
    for (let i = 0; i < NU; i++) C.push(this.curve(i / NU * LOOP_TAU));
    for (let i = 0; i < NU; i++){
      const a = C[(i - 1 + NU) % NU], b = C[(i + 1) % NU];
      const dx = b[0]-a[0], dy = b[1]-a[1], dz = b[2]-a[2];
      const l = Math.hypot(dx, dy, dz) || 1;
      T.push([dx/l, dy/l, dz/l]);
    }
    const r = 0.152 * this.thick;
    this.verts = []; this.norms = []; this.frameN = []; this.frameB = [];
    for (let i = 0; i < NU; i++){
      const t = T[i];
      let nx = t[1]*ref[2] - t[2]*ref[1];
      let ny = t[2]*ref[0] - t[0]*ref[2];
      let nz = t[0]*ref[1] - t[1]*ref[0];
      const ln = Math.hypot(nx, ny, nz) || 1;
      nx/=ln; ny/=ln; nz/=ln;
      const bx = t[1]*nz - t[2]*ny, by = t[2]*nx - t[0]*nz, bz = t[0]*ny - t[1]*nx;
      const rv = [], rn = [];
      for (let j = 0; j < NV; j++){
        const a = j / NV * LOOP_TAU, ca = Math.cos(a), sa = Math.sin(a);
        const vx = nx*ca + bx*sa, vy = ny*ca + by*sa, vz = nz*ca + bz*sa;
        rv.push([C[i][0]+vx*r, C[i][1]+vy*r, C[i][2]+vz*r]);
        rn.push([vx, vy, vz]);
      }
      this.verts.push(rv); this.norms.push(rn);
      this.frameN.push([nx, ny, nz]); this.frameB.push([bx, by, bz]);
    }
    this.center = C;
  }
  setMode(m){ if (m === this.mode) return; this.mode = m; this.build(); }
  setThick(v){ this.thick = v; this.build(); }
  setFlight(on){
    this.flight = on;
    if (!on){ this.yaw = 0; this.tilt = 0.27; this.autoSpin = true; }
  }
  setBolts(l){ this.boltLevel = l; if (!l){ this.bolts.length = 0; this.arcs.length = 0; } }

  /* ==================== camera ==================== */
  rotM(){
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    const cx = Math.cos(this.tilt), sx = Math.sin(this.tilt);
    return [cy, 0, sy, sy*sx, cx, -cy*sx, -sy*cx, sx, cy*cx];
  }
  applyRot(m, x, y, z){
    return [m[0]*x + m[1]*y + m[2]*z, m[3]*x + m[4]*y + m[5]*z, m[6]*x + m[7]*y + m[8]*z];
  }
  sampleLocal(u){
    const NU = this.NU;
    const fi = (((u % 1) + 1) % 1) * NU;
    const i0 = Math.floor(fi) % NU, i1 = (i0 + 1) % NU, t = fi - Math.floor(fi);
    return {
      p: lerp3(this.center[i0], this.center[i1], t),
      b: lerp3(this.frameB[i0], this.frameB[i1], t),
      n: lerp3(this.frameN[i0], this.frameN[i1], t)
    };
  }
  sampleWorld(u){
    const s = this.sampleLocal(u), m = this._m;
    return {
      p: this.applyRot(m, s.p[0], s.p[1], s.p[2]),
      b: this.applyRot(m, s.b[0], s.b[1], s.b[2])
    };
  }
  camera(){
    if (!this.flight) return { pos:[0, 0, 3.42 * this.zoom], look:[0, 0, 0], roll:0 };
    /* riding the ribbon — the camera flies just outside the conduit and
       looks ahead down the track, so the ∞ bends away into the void */
    const a = this.sampleWorld(this.flightT);
    const c = this.sampleWorld(this.flightT + 0.048);
    const off = (0.44 + 1.15 * (0.5 + 0.5 * Math.cos(this.flightT * LOOP_TAU * 2))) * this.zoom;
    return {
      pos:[a.p[0] + a.b[0]*off, a.p[1] + a.b[1]*off, a.p[2] + a.b[2]*off],
      look:c.p,
      roll:0.34 * Math.sin(this.flightT * LOOP_TAU * 2 + 0.35)
    };
  }
  basis(cam){
    let fx = cam.look[0]-cam.pos[0], fy = cam.look[1]-cam.pos[1], fz = cam.look[2]-cam.pos[2];
    const fl = Math.hypot(fx, fy, fz) || 1; fx/=fl; fy/=fl; fz/=fl;
    let rx = -fz, ry = 0, rz = fx;
    const rl = Math.hypot(rx, ry, rz) || 1; rx/=rl; ry/=rl; rz/=rl;
    let ux = ry*fz - rz*fy, uy = rz*fx - rx*fz, uz = rx*fy - ry*fx;
    if (cam.roll){
      const c = Math.cos(cam.roll), s = Math.sin(cam.roll);
      const nrx = rx*c + ux*s, nry = ry*c + uy*s, nrz = rz*c + uz*s;
      const nux = ux*c - rx*s, nuy = uy*c - ry*s, nuz = uz*c - rz*s;
      rx=nrx; ry=nry; rz=nrz; ux=nux; uy=nuy; uz=nuz;
    }
    return { fw:[fx,fy,fz], rt:[rx,ry,rz], up:[ux,uy,uz] };
  }
  buildProjection(cam){
    const m = this._m = this.rotM();
    const b = this._b = this.basis(cam);
    this._cam = cam;
    const NU = this.NU, NV = this.NV;
    const focal = Math.min(this.W, this.H) * 1.58;
    const cx = this.W / 2, cy = this.H / 2;
    const pv = [], pn = [], pc = [];
    for (let i = 0; i < NU; i++){
      const vr = [], nr = [];
      for (let j = 0; j < NV; j++){
        const p = this.verts[i][j], n = this.norms[i][j];
        const X = m[0]*p[0]+m[1]*p[1]+m[2]*p[2];
        const Y = m[3]*p[0]+m[4]*p[1]+m[5]*p[2];
        const Z = m[6]*p[0]+m[7]*p[1]+m[8]*p[2];
        const dx = X-cam.pos[0], dy = Y-cam.pos[1], dz = Z-cam.pos[2];
        const vz = dx*b.fw[0] + dy*b.fw[1] + dz*b.fw[2];
        const vx = dx*b.rt[0] + dy*b.rt[1] + dz*b.rt[2];
        const vy = dx*b.up[0] + dy*b.up[1] + dz*b.up[2];
        const k = focal / Math.max(0.25, vz);
        vr.push([cx + vx*k, cy - vy*k, vz]);
        nr.push([m[0]*n[0]+m[1]*n[1]+m[2]*n[2], m[3]*n[0]+m[4]*n[1]+m[5]*n[2], m[6]*n[0]+m[7]*n[1]+m[8]*n[2]]);
      }
      pv.push(vr); pn.push(nr);
      const c0 = this.center[i];
      const CX = m[0]*c0[0]+m[1]*c0[1]+m[2]*c0[2];
      const CY = m[3]*c0[0]+m[4]*c0[1]+m[5]*c0[2];
      const CZ = m[6]*c0[0]+m[7]*c0[1]+m[8]*c0[2];
      const dx = CX-cam.pos[0], dy = CY-cam.pos[1], dz = CZ-cam.pos[2];
      const vz = dx*b.fw[0] + dy*b.fw[1] + dz*b.fw[2];
      const vx = dx*b.rt[0] + dy*b.rt[1] + dz*b.rt[2];
      const vy = dx*b.up[0] + dy*b.up[1] + dz*b.up[2];
      const k = focal / Math.max(0.25, vz);
      pc.push([cx + vx*k, cy - vy*k, vz]);
    }
    this.pv = pv; this.pn = pn; this.pc = pc;
  }
  projLocal(p){
    const m = this._m, b = this._b, cam = this._cam;
    const X = m[0]*p[0]+m[1]*p[1]+m[2]*p[2];
    const Y = m[3]*p[0]+m[4]*p[1]+m[5]*p[2];
    const Z = m[6]*p[0]+m[7]*p[1]+m[8]*p[2];
    const dx = X-cam.pos[0], dy = Y-cam.pos[1], dz = Z-cam.pos[2];
    const vz = dx*b.fw[0] + dy*b.fw[1] + dz*b.fw[2];
    const vx = dx*b.rt[0] + dy*b.rt[1] + dz*b.rt[2];
    const vy = dx*b.up[0] + dy*b.up[1] + dz*b.up[2];
    const k = Math.min(this.W, this.H) * 1.58 / Math.max(0.25, vz);
    return [this.W/2 + vx*k, this.H/2 - vy*k, vz];
  }

  /* ==================== controls ==================== */
  bindControls(){
    const cv = this.cv;
    cv.style.cursor = 'grab';
    cv.addEventListener('mousedown', e => {
      this.drag = { x:e.clientX, y:e.clientY };
      this.autoSpin = false; this.manual = true;
      cv.style.cursor = 'grabbing';
      e.preventDefault();
    });
    window.addEventListener('mousemove', e => {
      if (!this.drag) return;
      const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
      this.drag = { x:e.clientX, y:e.clientY };
      this.yaw += dx * 0.010;
      this.tilt = Math.max(-1.0, Math.min(1.0, this.tilt + dy * 0.007));
    });
    window.addEventListener('mouseup', () => { this.drag = null; cv.style.cursor = 'grab'; });
    cv.addEventListener('wheel', e => {
      e.preventDefault();
      this.zoom = Math.max(0.55, Math.min(2.6, this.zoom * (1 - e.deltaY * 0.0013)));
    }, { passive:false });
    cv.addEventListener('dblclick', () => {
      this.yaw = 0; this.tilt = 0.27; this.zoom = 1; this.autoSpin = true; this.manual = false;
    });
  }
  resize(){
    const r = this.cv.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(200, r.width), h = Math.max(140, r.height);
    if (this.cv.width !== Math.round(w * dpr) || this.cv.height !== Math.round(h * dpr)){
      this.cv.width = Math.round(w * dpr); this.cv.height = Math.round(h * dpr);
    }
    this.W = this.cv.width; this.H = this.cv.height; this.dpr = dpr;
  }
  setValue(p){ this.target = Math.max(0, Math.min(100, p || 0)); }

  /* ==================== simulation ==================== */
  spawnBolt(){
    this.bolts.push({
      u: Math.random(), dir: Math.random() < 0.5 ? -1 : 1,
      sp: 0.14 + Math.random() * 0.22,
      len: 0.09 + Math.random() * 0.15,
      seed: (Math.random() * 1e6) | 0,
      life: 1, decay: 0.5 + Math.random() * 0.55,
      col: Math.random() < 0.45 ? LOOP_LIME : (Math.random() < 0.6 ? LOOP_CYAN : LOOP_HOT)
    });
  }
  spawnArc(){
    const cols = [[255,255,255], LOOP_CYAN, LOOP_LIME, LOOP_VIOLET, LOOP_HOT];
    this.arcs.push({
      ang: Math.random() * LOOP_TAU,
      x: Math.random() * this.W, y: Math.random() * this.H,
      len: Math.max(this.W, this.H) * (0.7 + Math.random() * 0.7),
      seed: (Math.random() * 1e6) | 0,
      life: 1, decay: 1.5 + Math.random() * 1.3,
      col: cols[(Math.random() * cols.length) | 0]
    });
    this.flash = 1;
    this.shake = 1;
  }
  step(dt){
    this.dt = dt;
    this.pct += (this.target - this.pct) * Math.min(1, dt * 4);
    this.drift += dt;
    if (this.autoSpin){
      if (this.flight){ this.yaw += dt * 0.12; this.tilt = 0.24 + Math.sin(this.drift * 0.31) * 0.16; }
      else { this.yaw = Math.sin(this.drift * 0.33) * 0.46; this.tilt = 0.27 + Math.sin(this.drift * 0.22) * 0.20; }
    }
    if (this.flight) this.flightT = (this.flightT + dt * 0.052) % 1;
    for (const mo of this.motes) mo.a += mo.sp * dt;

    if (this.boltLevel > 0){
      this.arcCd -= dt;
      if (this.arcCd <= 0){
        this.arcCd = this.boltLevel === 2 ? 1.0 + Math.random() * 1.8 : 3.0 + Math.random() * 3.0;
        this.spawnArc();
      }
      this.boltCd -= dt;
      if (this.boltCd <= 0){
        this.boltCd = this.boltLevel === 2 ? 0.09 + Math.random() * 0.15 : 0.45 + Math.random() * 0.7;
        this.spawnBolt();
      }
    }
    for (const b of this.bolts) b.u += b.dir * b.sp * dt;
    for (const b of this.bolts) b.life -= b.decay * dt;
    this.bolts = this.bolts.filter(b => b.life > 0);
    for (const a of this.arcs) a.life -= a.decay * dt;
    this.arcs = this.arcs.filter(a => a.life > 0);
    this.flash = Math.max(0, this.flash - dt * 2.1);
    this.shake = Math.max(0, this.shake - dt * 3.0);

    if (this.flight){
      for (const s of this.streaks){
        s.r += s.sp * dt * 0.6;
        if (s.r > 1.3){ s.r = 0.02; s.a = Math.random() * LOOP_TAU; }
      }
    }
  }

  /* ==================== rendering ==================== */
  draw(){
    this.resize();
    const ctx = this.ctx;
    const cam = this.camera();
    this.buildProjection(cam);

    ctx.save();
    if (this.shake > 0.01){
      const k = this.shake * this.shake * 5 * this.dpr;
      ctx.translate((Math.random() - 0.5) * k, (Math.random() - 0.5) * k);
    }
    ctx.clearRect(-20, -20, this.W + 40, this.H + 40);

    /* ambient halo — the loop breathes light into the void */
    const halo = ctx.createRadialGradient(this.W/2, this.H/2, this.H*.05, this.W/2, this.H/2, this.H*.72);
    halo.addColorStop(0, 'rgba(34,230,255,.13)');
    halo.addColorStop(.45, 'rgba(197,107,255,.07)');
    halo.addColorStop(1, 'transparent');
    ctx.fillStyle = halo; ctx.fillRect(0, 0, this.W, this.H);

    this.drawStreaks(ctx);
    this.drawRibbon(ctx);
    this.drawSheen(ctx);
    this.drawMotes(ctx);
    this.drawStages(ctx);
    this.drawHead(ctx);
    this.drawBolts(ctx);
    this.drawArcs(ctx);
    this.drawFlash(ctx);

    ctx.restore();
  }
  /* the ribbon itself — solid quads, lit, so it reads round and solid */
  drawRibbon(ctx){
    const NU = this.NU, NV = this.NV;
    const frac = this.pct / 100;
    const L = [0.30, 0.66, 0.69];
    const Hv = [0.21, 0.47, 0.82];
    const quads = [];
    for (let i = 0; i < NU; i++){
      const i2 = (i + 1) % NU;
      const u = (i + 0.5) / NU;
      for (let j = 0; j < NV; j++){
        const j2 = (j + 1) % NV;
        const p = [this.pv[i][j], this.pv[i2][j], this.pv[i2][j2], this.pv[i][j2]];
        const depth = (p[0][2] + p[1][2] + p[2][2] + p[3][2]) / 4;
        quads.push({ p, n:this.pn[i][j], depth, u });
      }
    }
    quads.sort((a, b) => b.depth - a.depth);
    this.sheen = [];
    for (const q of quads){
      const n = q.n;
      const ndl = Math.max(0, n[0]*L[0] + n[1]*L[1] + n[2]*L[2]);
      const ndh = Math.max(0, n[0]*Hv[0] + n[1]*Hv[1] + n[2]*Hv[2]);
      const spec = Math.pow(ndh, 26);
      const fres = Math.pow(1 - Math.min(1, Math.abs(n[2])), 2.4);
      const charged = q.u <= frac;
      let base;
      if (charged){
        const t = frac > 0.001 ? Math.min(1, q.u / frac) : 0;
        base = mixc(LOOP_LIME, LOOP_CYAN, t);
      } else {
        base = mixc([14, 24, 50], LOOP_VIOLET, 0.45);
      }
      const col = mixc(base, [255,255,255], spec * 0.72);
      const lit = charged ? 1 : 0.32;
      const alpha = Math.min(1, (0.15 + ndl*0.48 + spec*0.5 + fres*0.42) * lit + (charged ? 0.08 : 0));
      const p = q.p;
      ctx.beginPath();
      ctx.moveTo(p[0][0], p[0][1]);
      ctx.lineTo(p[1][0], p[1][1]);
      ctx.lineTo(p[2][0], p[2][1]);
      ctx.lineTo(p[3][0], p[3][1]);
      ctx.closePath();
      ctx.fillStyle = rgb(col, alpha);
      ctx.fill();
      ctx.strokeStyle = rgb(col, alpha * 0.85);
      ctx.lineWidth = this.dpr;
      ctx.stroke();
      if (spec > 0.5) this.sheen.push({ p, col, spec });
    }
  }
  /* wet highlight — the single trick that makes a tube read as round */
  drawSheen(ctx){
    if (!this.sheen || !this.sheen.length) return;
    ctx.globalCompositeOperation = 'lighter';
    for (const s of this.sheen){
      ctx.strokeStyle = rgb(s.col, .04 + s.spec * .13);
      ctx.lineWidth = 7 * this.dpr;
      ctx.beginPath();
      ctx.moveTo(s.p[0][0], s.p[0][1]);
      for (let i = 1; i < 4; i++) ctx.lineTo(s.p[i][0], s.p[i][1]);
      ctx.closePath(); ctx.stroke();
    }
    ctx.globalCompositeOperation = 'source-over';
  }
  drawMotes(ctx){
    const NU = this.NU;
    for (const mo of this.motes){
      const fi = mo.u * NU;
      const i0 = Math.floor(fi) % NU, i1 = (i0 + 1) % NU, t = fi - Math.floor(fi);
      const c0 = this.center[i0], c1 = this.center[i1];
      const n0 = this.frameN[i0], n1 = this.frameN[i1];
      const b0 = this.frameB[i0], b1 = this.frameB[i1];
      const ca = Math.cos(mo.a) * mo.r, sa = Math.sin(mo.a) * mo.r;
      const lx = lerp3(c0, c1, t)[0] + lerp3(n0, n1, t)[0]*ca + lerp3(b0, b1, t)[0]*sa;
      const ly = lerp3(c0, c1, t)[1] + lerp3(n0, n1, t)[1]*ca + lerp3(b0, b1, t)[1]*sa;
      const lz = lerp3(c0, c1, t)[2] + lerp3(n0, n1, t)[2]*ca + lerp3(b0, b1, t)[2]*sa;
      const P = this.projLocal([lx, ly, lz]);
      if (P[2] < 0.2) continue;
      const sc = Math.min(1, P[2] / 2.4);
      const g = ctx.createRadialGradient(P[0], P[1], 0, P[0], P[1], mo.s * 6 * this.dpr * sc);
      g.addColorStop(0, 'rgba(150,240,255,.75)');
      g.addColorStop(1, 'rgba(150,240,255,0)');
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(P[0], P[1], mo.s * 6 * this.dpr * sc, 0, 7); ctx.fill();
      ctx.fillStyle = 'rgba(220,250,255,.9)';
      ctx.beginPath(); ctx.arc(P[0], P[1], mo.s * .8 * this.dpr * sc, 0, 7); ctx.fill();
    }
  }
  /* the five stations of the agent loop */
  drawStages(ctx){
    const frac = this.pct / 100;
    let ai = 0, best = 9;
    for (let s = 0; s < LOOP_STAGES.length; s++){
      const u = s / LOOP_STAGES.length;
      const d = Math.abs(((u - frac + 1.5) % 1) - 0.5);
      if (d < best){ best = d; ai = s; }
    }
    this.activeStage = ai;
    for (let s = 0; s < LOOP_STAGES.length; s++){
      const u = s / LOOP_STAGES.length;
      const fi = u * this.NU;
      const i0 = Math.floor(fi) % this.NU, i1 = (i0 + 1) % this.NU, t = fi - Math.floor(fi);
      const a = this.pc[i0], b = this.pc[i1];
      const x = a[0] + (b[0]-a[0])*t, y = a[1] + (b[1]-a[1])*t, z = a[2] + (b[2]-a[2])*t;
      if (z < 0.2) continue;
      const on = s === ai;
      const col = on ? LOOP_HOT : LOOP_CYAN;
      const sc = Math.min(1, z / 2.2);
      const r = (on ? 4.4 : 2.7) * this.dpr * sc;
      const g = ctx.createRadialGradient(x, y, 0, x, y, r * 3.6);
      g.addColorStop(0, rgb(col, on ? .95 : .45));
      g.addColorStop(1, rgb(col, 0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(x, y, r * 3.6, 0, 7); ctx.fill();
      ctx.fillStyle = rgb(on ? [255,255,255] : col, on ? 1 : .7);
      ctx.beginPath(); ctx.arc(x, y, r * .7, 0, 7); ctx.fill();
      if (on){
        ctx.font = '800 ' + (9 * this.dpr) + 'px ' + getComputedStyle(document.body).fontFamily;
        ctx.textAlign = 'center';
        ctx.fillStyle = 'rgba(235,250,255,.92)';
        ctx.fillText(LOOP_STAGES[s], x, y - r * 3.6 - 5 * this.dpr);
        ctx.textAlign = 'left';
      }
    }
  }
  /* the charge head riding the curve */
  drawHead(ctx){
    const frac = this.pct / 100;
    if (frac < 0.004) return;
    const NU = this.NU;
    const fi = frac * NU;
    const i0 = Math.floor(fi) % NU, i1 = (i0 + 1) % NU, t = fi - Math.floor(fi);
    const a = this.pc[i0], b = this.pc[i1];
    const hx = a[0] + (b[0]-a[0])*t, hy = a[1] + (b[1]-a[1])*t, hz = a[2] + (b[2]-a[2])*t;
    if (hz < 0.2) return;
    for (let k = 0; k < 13; k++){
      const u = frac - k * 0.0055;
      const f2 = u * NU;
      const j0 = Math.floor(f2) % NU, j1 = (j0 + 1) % NU, t2 = f2 - Math.floor(f2);
      const p0 = this.pc[j0], p1 = this.pc[j1];
      const x = p0[0] + (p1[0]-p0[0])*t2, y = p0[1] + (p1[1]-p0[1])*t2;
      ctx.fillStyle = 'rgba(255,255,255,' + (0.5 * (1 - k/13)).toFixed(3) + ')';
      ctx.beginPath(); ctx.arc(x, y, (2.7 - k*0.16) * this.dpr, 0, 7); ctx.fill();
    }
    const g = ctx.createRadialGradient(hx, hy, 0, hx, hy, 28 * this.dpr);
    g.addColorStop(0, 'rgba(255,255,255,.92)');
    g.addColorStop(.28, rgb(LOOP_CYAN, .55));
    g.addColorStop(1, rgb(LOOP_CYAN, 0));
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(hx, hy, 28 * this.dpr, 0, 7); ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.beginPath(); ctx.arc(hx, hy, 3 * this.dpr, 0, 7); ctx.fill();
  }
  /* discharges racing INSIDE the conduit */
  drawBolts(ctx){
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    for (const b of this.bolts){
      const flick = 0.5 + 0.5 * Math.abs(Math.sin(this.drift * 43 + b.seed * 0.001));
      const a0 = Math.max(0, Math.min(1, b.life)) * flick;
      if (a0 <= 0.03) continue;
      const pts = [];
      const N = 12;
      for (let i = 0; i <= N; i++){
        const u = b.u - (i / N) * b.len * b.dir;
        const s = this.sampleLocal(u);
        const jx = (hash2(b.seed, i) - 0.5) * 0.19;
        const jy = (hash2(b.seed, 60 + i) - 0.5) * 0.19;
        const jz = (hash2(b.seed, 120 + i) - 0.5) * 0.19;
        const P = this.projLocal([s.p[0]+jx, s.p[1]+jy, s.p[2]+jz]);
        if (P[2] < 0.12){ pts.length = 0; break; }
        pts.push(P);
      }
      if (pts.length < 3) continue;
      this.strokeBolt(ctx, pts, b.col, a0, 1);
      /* forks peel off the main channel */
      const nf = 2 + ((hash2(b.seed, 7) * 2) | 0);
      for (let k = 0; k < nf; k++){
        const idx = 2 + Math.floor(hash2(b.seed, 20 + k) * Math.max(1, pts.length - 4));
        const base = pts[idx];
        let ang = hash2(b.seed, 40 + k) * Math.PI * 2;
        const segs = 3 + ((hash2(b.seed, 70 + k) * 2) | 0);
        const len = 11 + hash2(b.seed, 100 + k) * 20;
        const br = [];
        let cx = base[0], cy = base[1];
        for (let i = 0; i < segs; i++){
          ang += (hash2(b.seed, 140 + k * 9 + i) - 0.5) * 1.7;
          const l = len / segs;
          cx += Math.cos(ang) * l; cy += Math.sin(ang) * l;
          br.push([cx, cy]);
        }
        this.strokeBolt(ctx, br, b.col, a0 * 0.55, 0.45);
      }
    }
  }
  strokeBolt(ctx, pts, col, a0, scale){
    const c = a => 'rgba(' + col.join(',') + ',' + a.toFixed(3) + ')';
    const path = () => {
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    };
    path(); ctx.strokeStyle = c(0.07 * a0); ctx.lineWidth = 15 * scale * this.dpr; ctx.stroke();
    path(); ctx.strokeStyle = c(0.24 * a0); ctx.lineWidth = 6.5 * scale * this.dpr; ctx.stroke();
    path(); ctx.strokeStyle = c(0.9 * a0); ctx.lineWidth = 2.6 * scale * this.dpr; ctx.stroke();
    path(); ctx.strokeStyle = 'rgba(255,255,255,' + a0.toFixed(3) + ')'; ctx.lineWidth = 1.1 * scale * this.dpr; ctx.stroke();
    const tip = pts[pts.length - 1];
    const g = ctx.createRadialGradient(tip[0], tip[1], 0, tip[0], tip[1], 17 * this.dpr);
    g.addColorStop(0, 'rgba(255,255,255,' + (0.9 * a0).toFixed(3) + ')');
    g.addColorStop(.4, c(0.35 * a0));
    g.addColorStop(1, c(0));
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(tip[0], tip[1], 17 * this.dpr, 0, 7); ctx.fill();
  }
  /* manga-grade strikes slashing across the frame */
  drawArcs(ctx){
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    for (const a of this.arcs){
      const flick = 0.45 + 0.55 * Math.abs(Math.sin(this.drift * 37 + a.seed * 0.001));
      const al = Math.max(0, Math.min(1, a.life)) * flick;
      if (al <= 0.03) continue;
      const N = 16;
      const dx = Math.cos(a.ang), dy = Math.sin(a.ang);
      const px = -dy, py = dx;
      const pts = [];
      for (let i = 0; i <= N; i++){
        const t = i / N;
        const off = (i === 0 || i === N) ? 0 : (hash2(a.seed, i) - 0.5) * a.len * 0.30;
        pts.push([a.x + dx*a.len*t + px*off, a.y + dy*a.len*t + py*off]);
      }
      const c = v => 'rgba(' + a.col.join(',') + ',' + v.toFixed(3) + ')';
      const path = () => {
        ctx.beginPath();
        ctx.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      };
      path(); ctx.strokeStyle = c(0.09 * al); ctx.lineWidth = 22 * this.dpr; ctx.stroke();
      path(); ctx.strokeStyle = c(0.28 * al); ctx.lineWidth = 9 * this.dpr; ctx.stroke();
      path(); ctx.strokeStyle = 'rgba(255,255,255,' + (0.92 * al).toFixed(3) + ')'; ctx.lineWidth = 3.2 * this.dpr; ctx.stroke();
      /* forks */
      const nf = 2 + ((hash2(a.seed, 11) * 3) | 0);
      for (let k = 0; k < nf; k++){
        const idx = 2 + Math.floor(hash2(a.seed, 30 + k) * (pts.length - 4));
        const base = pts[idx];
        let ang = a.ang + (hash2(a.seed, 50 + k) - 0.5) * 2.4;
        const segs = 3 + ((hash2(a.seed, 80 + k) * 2) | 0);
        const len = 20 + hash2(a.seed, 110 + k) * 44;
        const br = [];
        let cx = base[0], cy = base[1];
        for (let i = 0; i < segs; i++){
          ang += (hash2(a.seed, 150 + k * 7 + i) - 0.5) * 1.9;
          const l = len / segs;
          cx += Math.cos(ang) * l; cy += Math.sin(ang) * l;
          br.push([cx, cy]);
        }
        const bp = () => {
          ctx.beginPath();
          ctx.moveTo(br[0][0], br[0][1]);
          for (let i = 1; i < br.length; i++) ctx.lineTo(br[i][0], br[i][1]);
        };
        bp(); ctx.strokeStyle = c(0.08 * al); ctx.lineWidth = 11 * this.dpr; ctx.stroke();
        bp(); ctx.strokeStyle = c(0.30 * al); ctx.lineWidth = 4 * this.dpr; ctx.stroke();
        bp(); ctx.strokeStyle = 'rgba(255,255,255,' + (0.7 * al).toFixed(3) + ')'; ctx.lineWidth = 1.4 * this.dpr; ctx.stroke();
      }
      /* impact burst */
      ctx.globalCompositeOperation = 'lighter';
      const bx = pts[0][0], by = pts[0][1];
      for (let i = 0; i < 14; i++){
        const ang = (i / 14) * LOOP_TAU + a.seed * 0.0001;
        const r0 = 8 * this.dpr, r1 = (26 + hash2(a.seed, 200 + i) * 60) * this.dpr;
        ctx.strokeStyle = c(0.28 * al);
        ctx.lineWidth = 1.4 * this.dpr;
        ctx.beginPath();
        ctx.moveTo(bx + Math.cos(ang)*r0, by + Math.sin(ang)*r0);
        ctx.lineTo(bx + Math.cos(ang)*r1, by + Math.sin(ang)*r1);
        ctx.stroke();
      }
      const bg = ctx.createRadialGradient(bx, by, 0, bx, by, 42 * this.dpr);
      bg.addColorStop(0, 'rgba(255,255,255,' + (0.7 * al).toFixed(3) + ')');
      bg.addColorStop(1, c(0));
      ctx.fillStyle = bg;
      ctx.beginPath(); ctx.arc(bx, by, 42 * this.dpr, 0, 7); ctx.fill();
      ctx.globalCompositeOperation = 'source-over';
    }
  }
  drawFlash(ctx){
    if (this.flash <= 0.01) return;
    ctx.globalCompositeOperation = 'lighter';
    const g = ctx.createRadialGradient(this.W/2, this.H/2, 0, this.W/2, this.H/2, Math.max(this.W, this.H)*0.75);
    g.addColorStop(0, 'rgba(150,225,255,' + (0.16 * this.flash).toFixed(3) + ')');
    g.addColorStop(1, 'rgba(150,225,255,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, this.W, this.H);
    ctx.globalCompositeOperation = 'source-over';
  }
  /* warp streaks — only while the camera is flying */
  drawStreaks(ctx){
    if (!this.flight) return;
    const cx = this.W/2, cy = this.H/2;
    const R = Math.max(this.W, this.H);
    ctx.globalCompositeOperation = 'lighter';
    for (const s of this.streaks){
      const r0 = s.r * R * 0.55;
      const r1 = r0 + s.l * R * 0.30;
      const x0 = cx + Math.cos(s.a)*r0, y0 = cy + Math.sin(s.a)*r0*0.82;
      const x1 = cx + Math.cos(s.a)*r1, y1 = cy + Math.sin(s.a)*r1*0.82;
      ctx.strokeStyle = 'rgba(140,225,255,' + (0.30 * (1 - s.r)).toFixed(3) + ')';
      ctx.lineWidth = 1.2 * this.dpr;
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    }
    ctx.globalCompositeOperation = 'source-over';
  }
}
