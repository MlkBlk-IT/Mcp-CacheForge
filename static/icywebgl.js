/* ============================================================================
   ICYCACHE WEBGL — moteur 3D temps réel (Three.js r128)
   ----------------------------------------------------------------------------
   Trois modules, tous pilotés par la VRAIE donnée du cache IcyCache :

     A. HumanNeuron (#globe)  — une VRAIE cellule neurale en 3D :
                                 soma organique, noyau, dendrites branchees,
                                 epines, axone myelinise (noeuds de Ranvier),
                                 terminaux axoniques. Chaque entree de cache
                                 est un noeud connecte au soma (synapse) et
                                 a ses voisins : TOUT est relie.

     B. AgentLoop  (#ring3d)   — le pipeline agent (PROMPT→NORMALIZE→KEY→
                                 CACHE→SERVE) sur une lemniscate de Gerono
                                 (figure-8) en 3D, avec stations, tete de
                                 charge, eclair, mode vol.

     C. Chart3D    (#bars3d…) — histogramme 3D extrude empile (hits / misses /
                                 bypass) + ruban 3D des tokens economises.

   Chaque module expose une API de controle complete. Les proprietes sont
   definies via des accesseurs : ecrire `core.autoSpin = false` suffit a
   declencher le recalcul, sans appel de methode.

   Convention de couleurs (alignee sur le CSS du dashboard) :
     cyan   #00ffff  → service depuis le cache (hit)
     fire   #ff6400  → generation payante (miss)
     violet #50b4ff  → bypass
     ember  #ffaa00  → tokens economises
     steel  #6b7a99  → depense / neutre
   ========================================================================== */
(function () {
  'use strict';

  if (typeof THREE === 'undefined') {
    console.warn('[IcyWebGL] Three.js absent — les modules 3D sont desactives.');
    return;
  }

  /* ---------------------------------------------------------------- palette
     Chaque role a SA couleur, alignee sur le CSS du dashboard :
       vert  #3dffa0  → service depuis le cache (hit)      = economie
       rouge #ff4d6d  → generation payante (miss)          = cout
       bleu  #4d9dff  → bypass
       jaune #ffd24a  → tokens economises
     La regle : une couleur = une information. Trois nuances de cyan
     ne permettaient pas de distinguer un hit d'un miss d'un bypass. */
  const C = {
    HIT:    0x3dffa0,
    MISS:   0xff4d6d,
    BYPASS: 0x4d9dff,
    SAVED:  0xffd24a,
    SPENT:  0x5a6a85,
    SHELL:  0x1d6b8c,
    GRID:   0x2f6f8f,
    MEMBRANE: 0x1b5e77,
    NEURITE:  0x2a9dbd,
    MYELIN:   0xbfe9ff,
    NUCLEUS:  0x7dffcf,
    AXON:     0x3fd0ff
  };

  const MODEL_COLORS = [
    0x3dffa0, 0xff4d6d, 0x4d9dff, 0xffd24a,
    0xff5fd2, 0xa98bff, 0x00e5ff, 0x2fe0c8
  ];

  const STAGES = ['PROMPT', 'NORMALIZE', 'KEY', 'CACHE', 'SERVE'];
  const SHAPES = ['loop', 'torus', 'helix', 'trefoil'];

  /* ---------------------------------------------------------------- helpers */
  const DPR = () => Math.min(window.devicePixelRatio || 1, 2);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  /* PRNG deterministe : le neurone doit avoir la MEME forme a chaque
     rechargement, sinon la silhouette change au reload (tres desorientant). */
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* Bruit lisse (somme de sinus) — deformation organique du soma. */
  function vnoise(x, y, z, seed) {
    return Math.sin(x * 1.73 + seed) * Math.cos(y * 1.91 - seed * 0.7) +
           Math.sin(z * 2.37 + seed * 1.3) * Math.cos(x * 1.11 + seed * 0.4) * 0.6 +
           Math.sin((x + y + z) * 2.9 + seed * 2.1) * 0.25;
  }

  /* Texture de halo radiale (partagee par tous les modules). */
  let _haloTex = null;
  function haloTexture() {
    if (_haloTex) return _haloTex;
    const s = 128, cv = document.createElement('canvas');
    cv.width = cv.height = s;
    const g = cv.getContext('2d');
    const grd = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    grd.addColorStop(0.00, 'rgba(255,255,255,1)');
    grd.addColorStop(0.18, 'rgba(255,255,255,.55)');
    grd.addColorStop(0.45, 'rgba(255,255,255,.16)');
    grd.addColorStop(1.00, 'rgba(255,255,255,0)');
    g.fillStyle = grd;
    g.fillRect(0, 0, s, s);
    _haloTex = new THREE.CanvasTexture(cv);
    return _haloTex;
  }

  /* Etiquette texte -> sprite. La taille est pilotee en pixels ecran par
     `fitLabelScale`, donc le texte reste lisible a TOUTE distance. */
  function labelSprite(text, color, scale) {
    const pad = 8, fs = 26;
    const cv = document.createElement('canvas');
    const g = cv.getContext('2d');
    g.font = '600 ' + fs + 'px "Segoe UI", system-ui, sans-serif';
    const w = Math.ceil(g.measureText(text).width) + pad * 2;
    cv.width = w; cv.height = fs + pad * 2;
    const g2 = cv.getContext('2d');
    g2.font = '600 ' + fs + 'px "Segoe UI", system-ui, sans-serif';
    g2.textBaseline = 'middle';
    g2.shadowColor = 'rgba(0,0,0,.85)';
    g2.shadowBlur = 6;
    g2.fillStyle = '#' + new THREE.Color(color).getHexString();
    g2.fillText(text, pad, cv.height / 2);
    const tex = new THREE.CanvasTexture(cv);
    tex.needsUpdate = true;
    const mat = new THREE.SpriteMaterial({
      map: tex, transparent: true, depthTest: false, depthWrite: false
    });
    const sp = new THREE.Sprite(mat);
    sp.scale.set((cv.width / cv.height) * scale, scale, 1);
    sp.raycast = function () {};          // les labels ne capturent pas le picking
    sp.userData.isLabel = true;
    sp.userData.aspect = cv.width / cv.height;
    sp.userData.px = fs + pad * 2;        // hauteur du sprite en pixels source
    return sp;
  }

  /* Echantillonne une polyligne en u \in [0,1] (longueur proportionnelle). */
  function samplePath(pts, u) {
    if (!pts || pts.length === 0) return new THREE.Vector3();
    if (pts.length === 1) return pts[0].clone();
    let total = 0;
    const segs = [];
    for (let i = 1; i < pts.length; i++) {
      const d = pts[i].distanceTo(pts[i - 1]);
      segs.push(d);
      total += d;
    }
    if (total <= 1e-9) return pts[0].clone();
    let target = ((u % 1) + 1) % 1 * total;
    for (let i = 0; i < segs.length; i++) {
      if (target <= segs[i] || i === segs.length - 1) {
        const f = segs[i] > 1e-9 ? target / segs[i] : 0;
        return pts[i].clone().lerp(pts[i + 1], clamp(f, 0, 1));
      }
      target -= segs[i];
    }
    return pts[pts.length - 1].clone();
  }

  /* ==========================================================================
     SAFE CURVE — parametrisation par abscisse curviligne maison.
     Three.js CatmullRomCurve3.getPointAt() peut renvoyer NaN quand deux
     echantillons consecutifs sont identiques (segment de longueur nulle).
     On recalcule donc nous-memes la table de longueurs : division protegee,
     indices bornes -> aucun NaN possible.
     ========================================================================== */
  function SafeCurve(fn, samples) {
    this.fn = fn;
    this.samples = samples || 900;
    this._built = false;
  }
  SafeCurve.prototype._build = function () {
    const n = this.samples;
    const pts = new Array(n + 1);
    const cum = new Float32Array(n + 1);
    let len = 0;
    for (let i = 0; i <= n; i++) {
      const p = this.fn(i / n);
      pts[i] = p;
      if (i > 0) {
        const d = p.distanceTo(pts[i - 1]);
        len += (isFinite(d) && d > 0) ? d : 0;
      }
      cum[i] = len;
    }
    this._pts = pts;
    this._cum = cum;
    this._len = len || 1;
    this._built = true;
  };
  SafeCurve.prototype.pointAt = function (u) {
    if (!this._built) this._build();
    u = ((u % 1) + 1) % 1;
    const target = u * this._len;
    const cum = this._cum;
    let lo = 0, hi = this.samples;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] < target) lo = mid + 1; else hi = mid;
    }
    let i = lo;
    if (i <= 0) return this._pts[0].clone();
    if (i > this.samples) i = this.samples;
    const l0 = cum[i - 1], l1 = cum[i];
    const seg = l1 - l0;
    const f = seg > 1e-12 ? (target - l0) / seg : 0;
    return this._pts[i - 1].clone().lerp(this._pts[i], f);
  };
  SafeCurve.prototype.tangentAt = function (u) {
    const a = this.pointAt(u - 0.004);
    const b = this.pointAt(u + 0.004);
    const t = new THREE.Vector3().subVectors(b, a);
    if (t.lengthSq() < 1e-12) return new THREE.Vector3(0, 1, 0);
    return t.normalize();
  };

  /* ==========================================================================
     TUBE A TAPER — tube de rayon variable le long d'une polyligne.
     (TubeGeometry de Three.js a un rayon constant ; on veut des dendrites
     qui s'effilent, un axone qui grossit vers le soma, etc.)
     ========================================================================== */
  function taperedTube(points, radiusAt, radialSeg, material) {
    const n = points.length;
    if (n < 2) return null;
    const T = [];
    for (let i = 0; i < n; i++) {
      const a = points[Math.max(0, i - 1)], b = points[Math.min(n - 1, i + 1)];
      const t = new THREE.Vector3().subVectors(b, a);
      if (t.lengthSq() < 1e-12) t.set(0, 1, 0);
      T.push(t.normalize());
    }
    /* Transport parallele : on fait "rouler" une normale le long de la courbe
       pour eviter la torsion de Frenet (qui vrille les tubes). */
    let N = new THREE.Vector3(0, 1, 0);
    if (Math.abs(N.dot(T[0])) > 0.9) N.set(1, 0, 0);
    N.crossVectors(T[0], N).normalize();

    const frames = [];
    for (let i = 0; i < n; i++) {
      if (i > 0) {
        const axis = new THREE.Vector3().crossVectors(T[i - 1], T[i]);
        const l = axis.length();
        if (l > 1e-8) {
          axis.divideScalar(l);
          const ang = Math.atan2(l, T[i - 1].dot(T[i]));
          N.applyAxisAngle(axis, ang);
        }
        N.addScaledVector(T[i], -N.dot(T[i])).normalize();
      }
      frames.push({ n: N.clone(), b: new THREE.Vector3().crossVectors(T[i], N) });
    }

    const verts = [], norms = [], uvs = [], idx = [];
    for (let i = 0; i < n; i++) {
      const r = radiusAt(i / (n - 1));
      const f = frames[i], p = points[i];
      for (let j = 0; j <= radialSeg; j++) {
        const th = (j / radialSeg) * Math.PI * 2;
        const cx = Math.cos(th), sy = Math.sin(th);
        const nx = f.n.x * cx + f.b.x * sy;
        const ny = f.n.y * cx + f.b.y * sy;
        const nz = f.n.z * cx + f.b.z * sy;
        verts.push(p.x + nx * r, p.y + ny * r, p.z + nz * r);
        norms.push(nx, ny, nz);
        uvs.push(j / radialSeg, i / (n - 1));
      }
    }
    for (let i = 0; i < n - 1; i++) {
      for (let j = 0; j < radialSeg; j++) {
        const a = i * (radialSeg + 1) + j;
        const b = a + radialSeg + 1;
        idx.push(a, b, a + 1, b, b + 1, a + 1);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
    geo.setAttribute('normal', new THREE.Float32BufferAttribute(norms, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    geo.setIndex(idx);
    return new THREE.Mesh(geo, material);
  }

  /* ==========================================================================
     ORBITE — rotation (drag), panoramique (shift+drag), zoom (molette),
     focus anime sur un point, auto-rotation.
     ========================================================================== */
  function makeOrbit(canvas, camera, dom) {
    const th0 = dom.theta === undefined ? 0.55 : dom.theta;
    const ph0 = dom.phi === undefined ? 0.22 : dom.phi;
    const st = {
      theta: th0, phi: ph0, dist: dom.dist,
      tTheta: th0, tPhi: ph0, tDist: dom.dist,
      target: new THREE.Vector3(0, 0, 0),
      tTarget: new THREE.Vector3(0, 0, 0),
      dragging: false, panning: false, lx: 0, ly: 0,
      auto: true, manual: false
    };
    const onDown = e => {
      st.dragging = true;
      st.manual = true; st.auto = false;
      st.panning = e.shiftKey || e.button === 1;
      st.lx = e.clientX; st.ly = e.clientY;
      canvas.style.cursor = st.panning ? 'move' : 'grabbing';
      e.preventDefault();
    };
    const onUp = () => {
      st.dragging = false; st.panning = false;
      canvas.style.cursor = 'grab';
    };
    const onMove = e => {
      if (!st.dragging) return;
      const dx = e.clientX - st.lx, dy = e.clientY - st.ly;
      st.lx = e.clientX; st.ly = e.clientY;
      if (st.panning) {
        const right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0);
        const up = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
        const k = st.dist * 0.0016;
        st.tTarget.addScaledVector(right, -dx * k).addScaledVector(up, dy * k);
      } else {
        st.tTheta -= dx * 0.008;
        st.tPhi = clamp(st.tPhi + dy * 0.008, -Math.PI / 2.15, Math.PI / 2.15);
      }
    };
    const onWheel = e => {
      e.preventDefault();
      st.tDist = clamp(st.tDist * (1 + e.deltaY * 0.0012), 6, 220);
    };
    canvas.addEventListener('pointerdown', onDown);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointermove', onMove);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    canvas.style.cursor = 'grab';

    return {
      state: st,
      reset: () => {
        st.tTheta = th0; st.tPhi = ph0; st.tDist = dom.dist;
        st.tTarget.set(0, 0, 0);
        st.manual = false; st.auto = true;
      },
      focus: (v, dist) => {
        st.tTarget.copy(v);
        if (dist) st.tDist = clamp(dist, 6, 220);
        st.manual = true; st.auto = false;
      },
      /* Recadre exactement la camera sur un ensemble de POINTS de contenu.
         Plus fin qu'une boite englobante : pour une rangee de barres, les
         coins de la boite surestiment l'encombrement reel (l'axe se tilt et
         la boite "fantome" repousse la camera trop loin). */
      framePoints: (points, margin) => {
        if (!points || !points.length) return;
        const b = new THREE.Box3();
        points.forEach(p => b.expandByPoint(p));
        const center = b.getCenter(new THREE.Vector3());
        const cp = Math.cos(st.phi), sp = Math.sin(st.phi);
        const fwd = new THREE.Vector3(-cp * Math.sin(st.theta), -sp, -cp * Math.cos(st.theta));
        let right = new THREE.Vector3().crossVectors(fwd, new THREE.Vector3(0, 1, 0));
        if (right.lengthSq() < 1e-8) right.set(1, 0, 0);
        right.normalize();
        const up = new THREE.Vector3().crossVectors(right, fwd).normalize();
        let mx = 0, my = 0, mz = 0;
        points.forEach(p => {
          const v = p.clone().sub(center);
          mx = Math.max(mx, Math.abs(v.dot(right)));
          my = Math.max(my, Math.abs(v.dot(up)));
          mz = Math.max(mz, Math.abs(v.dot(fwd)));
        });
        const tanV = Math.tan(camera.fov * Math.PI / 360);
        const tanH = tanV * (camera.aspect || 1);
        const d = Math.max(my / tanV, mx / tanH) + mz;
        st.tTarget.copy(center);
        st.target.copy(center);
        st.tDist = clamp(d * (margin || 1.08), 12, 220);
        st.dist = st.tDist;
      },
      frameBox: (box, margin) => {
        const center = box.getCenter(new THREE.Vector3());
        const pts = [];
        for (const x of [box.min.x, box.max.x])
          for (const y of [box.min.y, box.max.y])
            for (const z of [box.min.z, box.max.z]) pts.push(new THREE.Vector3(x, y, z));
        const cp = Math.cos(st.phi), sp = Math.sin(st.phi);
        const fwd = new THREE.Vector3(-cp * Math.sin(st.theta), -sp, -cp * Math.cos(st.theta));
        let right = new THREE.Vector3().crossVectors(fwd, new THREE.Vector3(0, 1, 0));
        if (right.lengthSq() < 1e-8) right.set(1, 0, 0);
        right.normalize();
        const up = new THREE.Vector3().crossVectors(right, fwd).normalize();
        let mx = 0, my = 0, mz = 0;
        pts.forEach(p => {
          const v = p.clone().sub(center);
          mx = Math.max(mx, Math.abs(v.dot(right)));
          my = Math.max(my, Math.abs(v.dot(up)));
          mz = Math.max(mz, Math.abs(v.dot(fwd)));
        });
        const tanV = Math.tan(camera.fov * Math.PI / 360);
        const tanH = tanV * (camera.aspect || 1);
        const d = Math.max(my / tanV, mx / tanH) + mz;
        st.tTarget.copy(center);
        st.target.copy(center);
        st.tDist = clamp(d * (margin || 1.12), 12, 220);
        st.dist = st.tDist;
      },
      update: (dt, autoSpeed) => {
        if (st.auto && !st.manual && !st.dragging) {
          if (dom.sway) {
            /* Balancement autour de l'angle de repos : la lemniscate reste
               lisible comme un "8" au lieu de tourner de profil et de
               disparaitre une fois sur deux. */
            st._clock = (st._clock || 0) + dt;
            st.tTheta = th0 + Math.sin(st._clock * (dom.swaySpeed || 0.32)) * (dom.swayAmp || 0.42);
          } else {
            st.tTheta += dt * (autoSpeed || 0.12);
          }
        }
        st.theta = lerp(st.theta, st.tTheta, 0.12);
        st.phi = lerp(st.phi, st.tPhi, 0.12);
        st.dist = lerp(st.dist, st.tDist, 0.12);
        st.target.lerp(st.tTarget, 0.12);
        const cp = Math.cos(st.phi), sp = Math.sin(st.phi);
        camera.position.set(
          cp * Math.sin(st.theta) * st.dist,
          sp * st.dist,
          cp * Math.cos(st.theta) * st.dist
        ).add(st.target);
        camera.lookAt(st.target);
      },
      dispose: () => {
        canvas.removeEventListener('pointerdown', onDown);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointermove', onMove);
        canvas.removeEventListener('wheel', onWheel);
      }
    };
  }

  /* Redimensionnement automatique : les canvas vivent dans des onglets qui
     peuvent etre masques au demarrage (clientWidth = 0). Un ResizeObserver
     les re-sync quand ils deviennent visibles. */
  function autoResize(obj) {
    const cv = obj.canvas;
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      const w = cv.clientWidth, h = cv.clientHeight;
      if (w > 0 && h > 0 && (w !== obj._w || h !== obj._h)) obj.resize();
    });
    ro.observe(cv);
    obj._ro = ro;
  }

  /* Raf loop qui se met en pause quand le canvas n'est plus visible. */
  function startLoop(fn) {
    let raf = 0, last = performance.now(), alive = true;
    function tick(now) {
      if (!alive) return;
      raf = requestAnimationFrame(tick);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const cv = fn.canvas;
      if (!cv) return;
      /* offsetParent === null => onglet/carte masque(e) : on ne rend pas. */
      if (cv.offsetParent === null && cv.style.position !== 'fixed') return;
      fn.step(dt, now);
    }
    raf = requestAnimationFrame(tick);
    return { stop: () => { alive = false; cancelAnimationFrame(raf); } };
  }

  /* Taille d'etiquette calee sur des PIXELS ECRAN : le texte garde la meme
     taille lisible quelle que soit la distance de camera. */
  function fitLabelScale(camera, canvas, sprites, px, target) {
    if (!sprites || !sprites.length) return;
    const h = canvas.clientHeight || 1;
    const dist = camera.position.distanceTo(target || camera.position) || 1;
    const fov = camera.fov * Math.PI / 180;
    const viewH = 2 * dist * Math.tan(fov / 2);
    const perPx = viewH / h;
    const s = perPx * (px || 14);
    for (const sp of sprites) {
      sp.scale.set((sp.userData.aspect || 1) * s, s, 1);
    }
  }

  /* ==========================================================================
     A. HUMAN NEURON — une cellule neurale complete en 3D
     ========================================================================== */
  function HumanNeuron(canvas) {
    const self = this;
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(DPR());
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 1000);
    this.camera.position.set(0, 10, 62);
    this.orbit = makeOrbit(canvas, this.camera, { dist: 62 });

    /* Lumieres : luisance du soma, reflet sur l'axone. */
    this.scene.add(new THREE.AmbientLight(0x2a4a5e, 1.25));
    const key = new THREE.PointLight(0x8fe8ff, 1.7, 220);
    key.position.set(14, 18, 22);
    this.scene.add(key);
    const rim = new THREE.PointLight(0xff7a2a, 1.0, 200);
    rim.position.set(-20, -10, -18);
    this.scene.add(rim);

    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.nodeGroup = new THREE.Group();
    this.group.add(this.nodeGroup);

    this.nodes = [];
    this.nodeMeshes = [];
    this.labels = [];
    this.synapses = [];
    this._items = [];
    this._models = [];
    this._pickCb = null;
    this._hoverCb = null;

    this._autoSpin = true;
    this._showLabels = true;
    this._showSynapses = true;
    this._showSpines = true;
    this._showMyelin = true;
    this._zoom = 1;
    this.nodeCount = 0;
    this.edgeCount = 0;
    this.legend = [];

    /* Vitalite : la cellule GRANDIT avec le contenu du cache. C'est la
       metrique la plus parlante du panneau — plus d'entrees, plus la
       cellule est developpee. */
    this._vitality = 1;
    this._vitalityTarget = 1;

    /* Potentiels d'action : une sphere lumineuse qui descend l'axone. */
    this.spikes = [];
    this._replayQueue = [];
    this._replayTimer = 0;

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2(-10, -10);
    this.hovered = null;

    canvas.addEventListener('pointermove', e => {
      const r = canvas.getBoundingClientRect();
      self.pointer.x = ((e.clientX - r.left) / r.width) * 2 - 1;
      self.pointer.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    });
    canvas.addEventListener('pointerleave', () => {
      self.pointer.set(-10, -10);
      self._setHover(null);
    });
    canvas.addEventListener('click', e => {
      const r = canvas.getBoundingClientRect();
      self.pointer.x = ((e.clientX - r.left) / r.width) * 2 - 1;
      self.pointer.y = -((e.clientY - r.top) / r.height) * 2 + 1;
      self.raycaster.setFromCamera(self.pointer, self.camera);
      const hits = self.raycaster.intersectObjects(self.nodeMeshes, false);
      if (self._pickCb) self._pickCb(hits.length ? hits[0].object.userData.item : null);
    });

    this._buildAnatomy();
    this._frameAnatomy();

    /* --- API publique ---------------------------------------------- */
    this.setGraph = function (items) { self._setGraph(items || []); };
    this.setOptions = function (o) {
      if (!o) return;
      if (o.autoSpin !== undefined) self.autoSpin = o.autoSpin;
      if (o.showLabels !== undefined) self.showLabels = o.showLabels;
      if (o.showSynapses !== undefined) self.showSynapses = o.showSynapses;
      if (o.showSpines !== undefined) self.showSpines = o.showSpines;
      if (o.showMyelin !== undefined) self.showMyelin = o.showMyelin;
    };
    this.onPick = function (cb) { self._pickCb = cb; };
    this.onHover = function (cb) { self._hoverCb = cb; };
    this.resetOrbit = function () { self.orbit.reset(); self._frameAnatomy(); };
    this.focusNode = function (item) {
      const m = self.nodeMeshes.find(x => x.userData.item === item);
      if (m) self.orbit.focus(m.position.clone(), Math.max(14, m.position.length() * 0.9));
    };
    this.resetLayout = function () { self._rebuildNodes(); };
    this.pulse = function (color) { self._pulse(color || C.HIT); };
    this.fireSoma = function () { self._fireSoma(); };
    /* Vitalite : la cellule se developpe avec la taille du cache. */
    this.setVitality = function (v) {
      self._vitalityTarget = clamp(+v || 1, 0.55, 1.9);
    };
    /* Rejoue un flux de requetes : chaque HIT traverse la cellule en vert
       (servi depuis le cache), chaque MISS en rouge (paye au modèle). */
    this.replay = function (events) {
      self._replayQueue = (events || []).slice(-24).reverse();
      self._replayTimer = 0;
    };
    this.stopReplay = function () { self._replayQueue = []; };
    this.resize = function () { self._resize(); };

    Object.defineProperty(this, 'autoSpin', {
      get: () => self._autoSpin,
      set: v => { self._autoSpin = !!v; self.orbit.state.auto = !!v; }
    });
    Object.defineProperty(this, 'showLabels', {
      get: () => self._showLabels,
      set: v => {
        self._showLabels = !!v;
        self.labels.forEach(l => { l.visible = self._showLabels; });
      }
    });
    Object.defineProperty(this, 'showSynapses', {
      get: () => self._showSynapses,
      set: v => {
        self._showSynapses = !!v;
        self.synapses.forEach(s => { s.visible = self._showSynapses; });
        if (self.synGroup) self.synGroup.visible = self._showSynapses;
      }
    });
    Object.defineProperty(this, 'showSpines', {
      get: () => self._showSpines,
      set: v => {
        self._showSpines = !!v;
        if (self.spineGroup) self.spineGroup.visible = self._showSpines;
      }
    });
    Object.defineProperty(this, 'showMyelin', {
      get: () => self._showMyelin,
      set: v => {
        self._showMyelin = !!v;
        if (self.myelinGroup) self.myelinGroup.visible = self._showMyelin;
      }
    });
    Object.defineProperty(this, 'zoom', {
      get: () => self._zoom,
      set: v => { self._zoom = clamp(+v || 1, 0.45, 2.6); }
    });

    this._resize();
    this._frameAnatomy();
    autoResize(this);
    this._loop = startLoop(this);
  }

  HumanNeuron.prototype._resize = function () {
    const cv = this.canvas;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;          // canvas masque : on garde la derniere taille
    this._w = w; this._h = h;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    /* Premiere mise a la taille reelle : on recadre une fois, pour que la
       cellule tienne dans le cadre (l'aspect change la distance requise). */
    if (!this._framed) { this._framed = true; this._frameAnatomy(); }
  };

  /* ------------------------------------------------------------------
     ANATOMIE : soma, noyau, dendrites, epines, axone, myeline, terminaux
     ------------------------------------------------------------------ */
  HumanNeuron.prototype._buildAnatomy = function () {
    const rng = mulberry32(20261024);
    const G = this.group;
    this.anatomy = new THREE.Group();
    G.add(this.anatomy);

    const R = 4.2;                       // rayon du soma
    this.somaRadius = R;

    /* --- SOMA : membrane irreguliere (bruit de surface) --------------
       SphereGeometry (indexee) et non IcosahedronGeometry : ce dernier est
       non-indexe, donc computeVertexNormals() produit des normales
       facettees et la membrane devient facettée au lieu d'organique. */
    const geo = new THREE.SphereGeometry(R, 72, 48);
    const pos = geo.attributes.position;
    const v = new THREE.Vector3();
    const seeds = [];
    for (let i = 0; i < 5; i++) seeds.push(new THREE.Vector3(rng() - 0.5, rng() - 0.5, rng() - 0.5).normalize());
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i);
      const n = v.clone().normalize();
      let d = 0;
      for (const s of seeds) d += Math.sin(n.dot(s) * 3.3 + s.x * 9) * 0.052;
      d += Math.sin(n.y * 5.5 + 1.3) * 0.028;
      v.copy(n).multiplyScalar(R * (1 + d));
      pos.setXYZ(i, v.x, v.y, v.z);
    }
    geo.computeVertexNormals();
    const soma = new THREE.Mesh(geo, new THREE.MeshPhongMaterial({
      color: 0x1b6d88, emissive: 0x062633, emissiveIntensity: 1,
      shininess: 85, specular: 0xbdf3ff, transparent: true, opacity: 0.95
    }));
    this.soma = soma;
    this.anatomy.add(soma);

    /* Halo membrane (rim light) */
    const rimShell = new THREE.Mesh(
      new THREE.IcosahedronGeometry(R * 1.045, 3),
      new THREE.MeshBasicMaterial({
        color: 0x4fd8ff, transparent: true, opacity: 0.14,
        blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.BackSide
      })
    );
    rimShell.raycast = function () {};
    this.anatomy.add(rimShell);
    this.rimShell = rimShell;

    /* --- NOYAU + nucleole -------------------------------------------- */
    const nucleus = new THREE.Mesh(
      new THREE.SphereGeometry(R * 0.44, 28, 20),
      new THREE.MeshBasicMaterial({ color: C.NUCLEUS, transparent: true, opacity: 0.9 })
    );
    nucleus.position.set(0.4, 0.2, 0.3);
    this.anatomy.add(nucleus);
    this.nucleus = nucleus;

    const nucleolus = new THREE.Mesh(
      new THREE.SphereGeometry(R * 0.17, 18, 14),
      new THREE.MeshBasicMaterial({ color: 0xffffff })
    );
    nucleolus.position.copy(nucleus.position).add(new THREE.Vector3(0.25, 0.15, 0.2));
    this.anatomy.add(nucleolus);

    const nHalo = new THREE.Sprite(new THREE.SpriteMaterial({
      map: haloTexture(), color: C.NUCLEUS, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.75
    }));
    nHalo.position.copy(nucleus.position);
    nHalo.scale.setScalar(R * 2.6);
    nHalo.raycast = function () {};
    this.anatomy.add(nHalo);
    this.nucleusHalo = nHalo;

    /* --- DENDRITES : arbres branches ---------------------------------- */
    const neuriteMat = new THREE.MeshPhongMaterial({
      color: C.NEURITE, emissive: 0x07303d, shininess: 70,
      specular: 0x9fe8ff, transparent: true, opacity: 0.97
    });
    const spineGroup = new THREE.Group();
    const myelinGroup = new THREE.Group();
    this.anatomy.add(spineGroup);
    this.anatomy.add(myelinGroup);
    this.spineGroup = spineGroup;
    this.myelinGroup = myelinGroup;

    const branches = [];
    /* `root` identifie le TRONC de dendrite d'origine. Sans ca, les indices
       debranches suivent l'ordre de parcours en profondeur (troncon, puis
       ses enfants) et tous les noeuds se retrouvent sur le meme tronc. */
    const grow = (origin, dir, len, rad, depth, rootId) => {
      const pts = [origin.clone()];
      const p = origin.clone();
      const d = dir.clone().normalize();
      const segs = 7;
      for (let i = 0; i < segs; i++) {
        d.x += (rng() - 0.5) * 0.62;
        d.y += (rng() - 0.5) * 0.62;
        d.z += (rng() - 0.5) * 0.62;
        d.y += 0.02;                       // tres leger biais vers le haut
        d.normalize();
        p.addScaledVector(d, len / segs);
        pts.push(p.clone());
      }
      branches.push({ pts: pts, r0: rad, r1: rad * 0.55, kind: 'dendrite', root: rootId });
      /* epines dendritiques */
      for (let s = 1; s < pts.length; s++) {
        if (rng() < 0.55) {
          const sp = new THREE.Mesh(
            new THREE.SphereGeometry(rad * 0.34, 7, 6),
            new THREE.MeshBasicMaterial({ color: 0x9fe8ff, transparent: true, opacity: 0.85 })
          );
          sp.position.copy(pts[s]).add(new THREE.Vector3(
            (rng() - 0.5) * 0.7, (rng() - 0.5) * 0.7, (rng() - 0.5) * 0.7));
          sp.raycast = function () {};
          spineGroup.add(sp);
        }
      }
      if (depth > 0) {
        const nB = rng() < 0.62 ? 2 : 1;
        for (let b = 0; b < nB; b++) {
          const idx = 2 + Math.floor(rng() * (segs - 2));
          const from = pts[idx];
          const nd = pts[idx + 1].clone().sub(from).normalize();
          const axis = new THREE.Vector3(rng() - 0.5, rng() - 0.5, rng() - 0.5).normalize();
          nd.applyAxisAngle(axis, (rng() - 0.5) * 1.9);
          grow(from, nd, len * 0.63, rad * 0.58, depth - 1, rootId);
        }
      }
    };

    const nDend = 7;
    for (let i = 0; i < nDend; i++) {
      const th = (i / nDend) * Math.PI * 2 + rng() * 0.5;
      /* Les troncs repartis sur toute la sphere (un peu plus haut que bas) :
         une distribution purement verticale donnerait un "sapin" colle au
         soma au lieu d'une cellule. */
      const ph = rng() * 1.45 - 0.42;
      const dir = new THREE.Vector3(
        Math.cos(th) * Math.cos(ph),
        Math.sin(ph) * 0.9,
        Math.sin(th) * Math.cos(ph)
      );
      const origin = dir.clone().multiplyScalar(R * 0.86);
      grow(origin, dir, 7.6 + rng() * 3.4, 0.62, 2, i);
    }

    /* --- AXONE + MYELINE + TERMINAUX ---------------------------------
       Longueur volontairement contenue : l'axone doit rester dans le meme
       gabarit que l'arbre dendritique, sinon il sort du cadre et la
       cellule n'est plus lisible. */
    const axDir = new THREE.Vector3(0.46, -0.74, 0.50).normalize();
    const axPts = [axDir.clone().multiplyScalar(R * 0.82)];
    let ap = axPts[0].clone();
    let ad = axDir.clone();
    for (let i = 0; i < 6; i++) {
      ad.x += (rng() - 0.5) * 0.2;
      ad.y += (rng() - 0.5) * 0.2;
      ad.z += (rng() - 0.5) * 0.2;
      ad.normalize();
      ap.addScaledVector(ad, 3.0);
      axPts.push(ap.clone());
    }
    branches.push({ pts: axPts, r0: 0.95, r1: 0.30, kind: 'axon', root: 100 });

    /* Gaine de myeline : segments elipsoidaux separes par les noeuds de Ranvier */
    const myelinMat = new THREE.MeshPhongMaterial({
      color: C.MYELIN, emissive: 0x143848, shininess: 120,
      specular: 0xffffff, transparent: true, opacity: 0.55
    });
    for (let i = 1; i < axPts.length - 1; i++) {
      const p = axPts[i], q = axPts[i + 1];
      const mid = p.clone().lerp(q, 0.5);
      const len = p.distanceTo(q);
      const g = new THREE.SphereGeometry(1, 12, 9);
      const m = new THREE.Mesh(g, myelinMat);
      m.position.copy(mid);
      m.scale.set(0.78, 0.78, len * 0.34);
      /* oriente le myelin selon l'axe */
      const dirq = q.clone().sub(p).normalize();
      m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), dirq);
      m.raycast = function () {};
      myelinGroup.add(m);
      /* noeud de Ranvier : petit anneau lumineux */
      const rn = new THREE.Mesh(
        new THREE.TorusGeometry(0.42, 0.07, 6, 14),
        new THREE.MeshBasicMaterial({ color: C.SAVED, transparent: true, opacity: 0.85 })
      );
      rn.position.copy(p);
      rn.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), dirq);
      rn.raycast = function () {};
      myelinGroup.add(rn);
    }

    /* Arborisation terminale + boutons synaptiques */
    const boutonMat = new THREE.MeshBasicMaterial({ color: 0xffd76b });
    const end = axPts[axPts.length - 1];
    const terminalTips = [];
    for (let b = 0; b < 5; b++) {
      const th = (b / 5) * Math.PI * 2 + rng();
      const bd = new THREE.Vector3(Math.cos(th) * 0.8, (rng() - 0.4) * 0.8, Math.sin(th) * 0.8).normalize();
      const tp = [end.clone()];
      let pp = end.clone(), dd = bd;
      for (let i = 0; i < 3; i++) {
        dd.x += (rng() - 0.5) * 0.5;
        dd.y += (rng() - 0.5) * 0.5;
        dd.z += (rng() - 0.5) * 0.5;
        dd.normalize();
        pp.addScaledVector(dd, 2.0);
        tp.push(pp.clone());
      }
      branches.push({ pts: tp, r0: 0.30, r1: 0.16, kind: 'terminal', root: 200 + b });
      const bouton = new THREE.Mesh(new THREE.SphereGeometry(0.52, 14, 10), boutonMat);
      bouton.position.copy(tp[tp.length - 1]);
      bouton.raycast = function () {};
      this.anatomy.add(bouton);
      const bh = new THREE.Sprite(new THREE.SpriteMaterial({
        map: haloTexture(), color: 0xffd76b, transparent: true,
        blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.7
      }));
      bh.position.copy(bouton.position);
      bh.scale.setScalar(3.2);
      bh.raycast = function () {};
      this.anatomy.add(bh);
      terminalTips.push(tp[tp.length - 1].clone());
    }

    /* Construction des tubes */
    this._branches = branches;
    for (const b of branches) {
      const mat = b.kind === 'axon'
        ? new THREE.MeshPhongMaterial({
            color: C.AXON, emissive: 0x0a3b4d, shininess: 90,
            specular: 0xd6f6ff, transparent: true, opacity: 0.97
          })
        : neuriteMat;
      const t = taperedTube(b.pts, u => lerp(b.r0, b.r1, u), 8, mat);
      if (!t) continue;
      t.raycast = function () {};
      this.anatomy.add(t);
    }

    /* Points d'ancrage : ou peuvent se poser les noeuds de cache.
       On memorise la branche d'appartenance pour repartir les noeuds sur des
       branches DIFFERENTES (sinon ils s'empilent tous sur la meme). */
    this.anchors = [];
    branches.forEach(b => {
      for (let i = 1; i < b.pts.length; i++) {
        /* On garde la polyligne de la branche : les noeuds y circulent
           ensuite (ce ne sont pas des points figes). */
        this.anchors.push({ p: b.pts[i], kind: b.kind, branch: b.root, path: b.pts });
      }
    });
    for (const t of terminalTips) this.anchors.push({ p: t, kind: 'bouton', branch: 300, path: null });
    /* L'axone, pour les potentials d'action. */
    this.axonPath = axPts;

    /* Fiche anatomique : c'est la metrique detaillee affichee dans le
       panneau « Human Neuron » (le nombre de dendrites, d'epines, de
       segments de myeline, de noeuds de Ranvier...). */
    this.stats = {
      somaDiameter: +(R * 2).toFixed(1),
      dendrites: nDend,
      branches: branches.length,
      spines: spineGroup.children.length,
      myelin: Math.floor(myelinGroup.children.length / 2),
      terminals: terminalTips.length,
      axonNodes: axPts.length,
      totalNodes: 0
    };
  };

  /* Recadre la camera sur la cellule : le cadrage suit la geometrie reelle,
     quelle que soit la longueur de l'axone ou des dendrites. */
  HumanNeuron.prototype._frameAnatomy = function () {
    if (!this.anatomy) return;
    const box = new THREE.Box3().setFromObject(this.anatomy);
    if (box.isEmpty()) return;
    this.orbit.frameBox(box, 1.02);
    const center = box.getCenter(new THREE.Vector3());
    const r = box.getBoundingSphere(new THREE.Sphere()).radius;
    this.camera.position.set(
      Math.sin(this.orbit.state.theta) * r * 1.5,
      r * 0.55,
      Math.cos(this.orbit.state.theta) * r * 1.5
    ).add(center);
    this.camera.lookAt(center);
  };

  /* ------------------------------------------------------------------
     NŒUDS DE CACHE + SYNAPSES
     ------------------------------------------------------------------ */
  HumanNeuron.prototype._setGraph = function (items) {
    const list = items.slice();
    const models = [];
    list.forEach(it => { if (models.indexOf(it.model) < 0) models.push(it.model); });
    list.forEach(it => {
      it._modelIdx = Math.max(0, models.indexOf(it.model || ''));
      it._color = MODEL_COLORS[it._modelIdx % MODEL_COLORS.length];
    });

    this.legend = models.map((m, i) => {
      const hex = MODEL_COLORS[i % MODEL_COLORS.length];
      return {
        model: m, hex: hex,
        col: [(hex >> 16) & 255, (hex >> 8) & 255, hex & 255],
        n: list.filter(x => x.model === m).length
      };
    });

    this._items = list;
    this._models = models;
    this._rebuildNodes();
  };

  HumanNeuron.prototype._rebuildNodes = function () {
    const self = this;
    const items = this._items;

    /* purge */
    this.nodeMeshes.forEach(m => {
      this.nodeGroup.remove(m);
      if (m.geometry) m.geometry.dispose();
      if (m.material) m.material.dispose();
      if (m.userData.halo) this.nodeGroup.remove(m.userData.halo);
    });
    this.labels.forEach(l => {
      this.nodeGroup.remove(l);
      if (l.material.map) l.material.map.dispose();
      if (l.material) l.material.dispose();
    });
    if (this.synGroup) {
      this.group.remove(this.synGroup);
      this.synGroup.traverse(o => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) o.material.dispose();
      });
    }
    this.nodeMeshes = [];
    this.labels = [];
    this.synapses = [];

    const synGroup = new THREE.Group();
    this.synGroup = synGroup;
    this.group.add(synGroup);

    const anchors = this.anchors || [];
    const maxHits = Math.max(1, ...items.map(i => i.hits || 0));
    this._linkSeen = new Set();
    const fract = x => x - Math.floor(x);

    /* Repartition round-robin sur les branches : le noeud i part sur la
       branche (i % nbBranches), ce qui evite l'empilement de tous les noeuds
       sur un seul dendrite et la superposition de leurs etiquettes. */
    const groups = [];
    const gmap = new Map();
    anchors.forEach(a => {
      if (!gmap.has(a.branch)) { gmap.set(a.branch, []); groups.push(gmap.get(a.branch)); }
      gmap.get(a.branch).push(a);
    });
    const pickAnchor = idx => {
      if (!groups.length) return { p: new THREE.Vector3(0, 0, 0), kind: 'dendrite' };
      const g = groups[idx % groups.length];
      const a = g[Math.floor(idx / groups.length) % g.length];
      return a;
    };

    /* Placement : les noeuds se repartissent sur l'arbre dendritique et les
       terminaux, pour qu'ils semblent faire partie de la cellule. */
    items.forEach((it, idx) => {
      const a = pickAnchor(idx);
      const jitter = new THREE.Vector3(
        fract(Math.sin(idx * 12.9898) * 43758.5453) - 0.5,
        fract(Math.sin(idx * 78.233) * 12345.6789) - 0.5,
        fract(Math.sin(idx * 39.346) * 87654.321) - 0.5
      );
      /* Le noeud vit sur sa branche : il y derive en permanence, mais
         autour de SA position (sinon tous les noeuds finissent par
         traverser le soma et leurs etiquettes s'empilent). */
      it._path = a.path || null;
      it._u0 = fract(Math.sin(idx * 45.164) * 9871.233);
      it._drift = 0.09 + (idx % 3) * 0.03;
      it._speed = 0.25 + (idx % 5) * 0.06;
      const pos = a.p.clone().add(jitter.multiplyScalar(0.55));
      it._pos = pos;
      it._anchorKind = a.kind;

      const scale = 0.34 + 0.62 * Math.sqrt((it.hits || 0) / maxHits);
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(scale, 16, 12),
        new THREE.MeshBasicMaterial({ color: it._color })
      );
      mesh.position.copy(pos);
      mesh.userData = { item: it, scale: scale, base: scale };
      this.nodeGroup.add(mesh);
      this.nodeMeshes.push(mesh);

      const halo = new THREE.Sprite(new THREE.SpriteMaterial({
        map: haloTexture(), color: it._color, transparent: true,
        blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.6
      }));
      halo.scale.setScalar(scale * 8);
      halo.position.copy(pos);
      halo.raycast = function () {};
      this.nodeGroup.add(halo);
      mesh.userData.halo = halo;
    });

    /* --- CONNEXIONS -------------------------------------------------
       1. chaque noeud est relie au soma par une synapse courbe (le "cable")
       2. chaque noeud est relie a ses 2 voisins les plus proches
       => l'ensemble forme un reseau continu : rien n'est isole.          */
    const somaPos = new THREE.Vector3(0, 0, 0);
    const linkMat = new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.55,
      blending: THREE.AdditiveBlending, depthWrite: false
    });
    const linkPos = [], linkCol = [];
    const c = new THREE.Color();

    /* Courbe quadratique de Bezier : le noeud -> le soma, avec un arc. */
    const addLink = (a, b, colA, colB, bow) => {
      const SEG = 16;
      const mid = a.clone().lerp(b, 0.5);
      const dir = b.clone().sub(a);
      let perp = dir.clone().cross(new THREE.Vector3(0, 1, 0));
      if (perp.lengthSq() < 1e-8) perp = dir.clone().cross(new THREE.Vector3(1, 0, 0));
      perp.normalize();
      const ctrl = mid.clone()
        .addScaledVector(perp, bow)
        .add(new THREE.Vector3(0, bow * 0.3, 0));
      let prev = a.clone();
      for (let i = 1; i <= SEG; i++) {
        const t = i / SEG, it = 1 - t;
        const cur = a.clone().multiplyScalar(it * it)
          .addScaledVector(ctrl, 2 * it * t)
          .addScaledVector(b, t * t);
        linkPos.push(prev.x, prev.y, prev.z, cur.x, cur.y, cur.z);
        c.set(colA).multiplyScalar(0.85); linkCol.push(c.r, c.g, c.b);
        c.set(colB).multiplyScalar(0.85); linkCol.push(c.r, c.g, c.b);
        prev = cur;
      }
    };

    items.forEach((it, i) => {
      addLink(it._pos, somaPos, it._color, C.AXON, 1.6 + (i % 5) * 0.35);
    });

    /* voisins les plus proches */
    for (let i = 0; i < items.length; i++) {
      const a = items[i]._pos;
      const d = [];
      for (let j = 0; j < items.length; j++) {
        if (i === j) continue;
        d.push([a.distanceTo(items[j]._pos), j]);
      }
      d.sort((x, y) => x[0] - y[0]);
      for (let k = 0; k < Math.min(2, d.length); k++) {
        const j = d[k][1];
        const key = i < j ? i + ':' + j : j + ':' + i;
        if (this._linkSeen.has(key)) continue;
        this._linkSeen.add(key);
        addLink(items[i]._pos, items[j]._pos, items[i]._color, items[j]._color, 0.9);
      }
    }

    if (linkPos.length) {
      const lg = new THREE.BufferGeometry();
      lg.setAttribute('position', new THREE.Float32BufferAttribute(linkPos, 3));
      lg.setAttribute('color', new THREE.Float32BufferAttribute(linkCol, 3));
      const lines = new THREE.LineSegments(lg, linkMat);
      lines.frustumCulled = false;
      lines.visible = this._showSynapses;
      synGroup.add(lines);
      this.synapses.push(lines);
    }

    /* Impulsions qui parcourent les synapses */
    const PULSE = Math.min(90, Math.max(18, items.length * 2));
    const pgeo = new THREE.BufferGeometry();
    pgeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(PULSE * 3), 3));
    this.pulseCount = PULSE;
    this.pulseGeo = pgeo;
    this.pulses = new THREE.Points(pgeo, new THREE.PointsMaterial({
      color: 0xffffff, size: 0.7, transparent: true, opacity: 0.9,
      blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true
    }));
    this.pulses.frustumCulled = false;
    synGroup.add(this.pulses);

    /* Etiquettes : uniquement les noeuds les plus consultes (lisibilite).
       Elles sont decalees en spirale autour du noeud, sinon elles se
       superposent quand plusieurs noeuds sont proches. Le detail complet
       d'un noeud est de toute facon dans l'inspecteur (survol). */
    const top = items.slice().sort((a, b) => (b.hits || 0) - (a.hits || 0)).slice(0, 4);
    top.forEach((it, li) => {
      const ang = li * 2.399963;             // angle d'or : repartition reguliere
      const off = new THREE.Vector3(
        Math.cos(ang) * 3.6,
        2.4 + (li % 3) * 1.1,
        Math.sin(ang) * 3.6
      );
      const lbl = labelSprite((it.label || it.model || '').slice(0, 22), it._color, 0.9);
      lbl.position.copy(it._pos).add(off);
      lbl.userData.item = it;
      lbl.userData.off = off.clone();
      lbl.visible = this._showLabels;
      this.nodeGroup.add(lbl);
      this.labels.push(lbl);
      /* Le noeud et son etiquette sont lies : l'etiquette suit le noeud. */
      const owner = this.nodeMeshes.find(x => x.userData.item === it);
      if (owner) owner.userData.label = lbl;
    });

    this.nodeCount = items.length;
    this.edgeCount = this._linkSeen.size + items.length;
    if (this.stats) this.stats.totalNodes = items.length;
  };

  HumanNeuron.prototype._pulse = function (color) {
    if (!this.nodeMeshes.length) return;
    const m = this.nodeMeshes[Math.floor(Math.random() * this.nodeMeshes.length)];
    m.userData.pulse = 1;
    m.material.color.setHex(color);
    if (m.userData.halo) m.userData.halo.material.color.setHex(color);
  };

  HumanNeuron.prototype._fireSoma = function () {
    if (!this.nodeMeshes.length) return;
    for (let i = 0; i < 8; i++) {
      const m = this.nodeMeshes[Math.floor(Math.random() * this.nodeMeshes.length)];
      m.userData.pulse = 1;
    }
    this.somaFlash = 1;
  };

  /* Un potentiel d'action : une lueur qui part du soma et descend l'axone
     jusqu'aux terminaux. C'est la traduction visuelle d'une requete qui
     traverse reellement le proxy. */
  HumanNeuron.prototype._fireSpike = function (color, speed) {
    const path = this.axonPath;
    if (!path || path.length < 2) return null;
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(0.55, 14, 10),
      new THREE.MeshBasicMaterial({ color: color, transparent: true, opacity: 1 })
    );
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({
      map: haloTexture(), color: color, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.9
    }));
    halo.scale.setScalar(5.5);
    mesh.add(halo);
    this.group.add(mesh);
    const sp = { mesh: mesh, t: 0, speed: speed || 0.55, color: color };
    this.spikes.push(sp);
    return sp;
  };

  /* Rejeu d'un flux : on enchaine les potentiels d'action, un par requete. */
  HumanNeuron.prototype._stepReplay = function (dt) {
    if (!this._replayQueue.length) return;
    this._replayTimer -= dt;
    if (this._replayTimer > 0) return;
    const ev = this._replayQueue.shift();
    if (!ev) return;
    const kind = String(ev.kind || ev.type || '').toUpperCase();
    const isHit = kind.includes('HIT');
    const color = isHit ? C.HIT : C.MISS;
    this._fireSpike(color, isHit ? 0.85 : 0.4);
    this.somaFlash = 1;
    this._replayTimer = 0.28;
  };

  HumanNeuron.prototype._setHover = function (mesh) {
    if (this.hovered === mesh) return;
    if (this.hovered) {
      const it = this.hovered.userData.item;
      this.hovered.material.color.setHex(it._color);
      this.hovered.scale.setScalar(this.hovered.userData.base);
    }
    this.hovered = mesh;
    if (mesh) {
      mesh.material.color.setHex(0xffffff);
      mesh.scale.setScalar(mesh.userData.base * 1.55);
    }
    if (this._hoverCb) {
      const it = mesh ? mesh.userData.item : null;
      this._hoverCb(it ? {
        label: it.label || '', model: it.model || 'unknown',
        hits: it.hits || 0, color: it._color, pos: it._pos
      } : null);
    }
  };

  HumanNeuron.prototype.step = function (dt, now) {
    const t = now * 0.001;
    this.orbit.update(dt, 0.10);
    if (this._zoom !== 1) {
      const d = this.camera.position.distanceTo(this.orbit.state.target);
      this.camera.position.sub(this.orbit.state.target)
        .multiplyScalar(1 + (this._zoom - 1) * 0.02)
        .add(this.orbit.state.target);
    }

    /* respiration du soma + flash */
    const breathe = 1 + Math.sin(t * 0.9) * 0.018;
    this.soma.scale.setScalar(breathe);
    this.rimShell.scale.setScalar(breathe * 1.01);
    this.nucleus.rotation.y += dt * 0.25;
    this.nucleus.rotation.x += dt * 0.12;
    if (this.somaFlash > 0) {
      this.somaFlash = Math.max(0, this.somaFlash - dt * 1.4);
      this.rimShell.material.opacity = 0.14 + this.somaFlash * 0.4;
    }
    this.nucleusHalo.material.opacity = 0.6 + Math.sin(t * 1.7) * 0.18;

    /* croissance : la cellule se developpe avec le cache, en douceur */
    if (Math.abs(this._vitality - this._vitalityTarget) > 0.001) {
      this._vitality += (this._vitalityTarget - this._vitality) * Math.min(1, dt * 0.6);
      this.anatomy.scale.setScalar(this._vitality);
    }

    /* rejeu du flux + potentials d'action */
    this._stepReplay(dt);
    for (let i = this.spikes.length - 1; i >= 0; i--) {
      const sp = this.spikes[i];
      sp.t += dt * sp.speed;
      if (sp.t >= 1) {
        this.group.remove(sp.mesh);
        sp.mesh.geometry.dispose();
        sp.mesh.material.dispose();
        this.spikes.splice(i, 1);
        continue;
      }
      const p = samplePath(this.axonPath, sp.t);
      sp.mesh.position.copy(p);
      sp.mesh.material.opacity = 1 - Math.pow(sp.t, 3);
      sp.mesh.scale.setScalar(1 + Math.sin(sp.t * Math.PI) * 0.5);
    }

    /* noeuds : circulation le long des neurites + respiration + impulsion */
    this.nodeMeshes.forEach((m, i) => {
      const it = m.userData.item;
      const base = m.userData.base;

      /* 1. le noeud derive le long de sa branche */
      if (it._path && it._path.length > 1) {
        const u = it._u0 + Math.sin(t * it._speed + i) * it._drift;
        const p = samplePath(it._path, u);
        it._pos.copy(p);
        m.position.copy(p);
        if (m.userData.halo) m.userData.halo.position.copy(p);
        if (m.userData.label) {
          const off = m.userData.label.userData.off;
          m.userData.label.position.copy(p).add(off);
        }
      }

      /* 2. respiration + flash */
      if (m.userData.pulse > 0) {
        m.userData.pulse -= dt * 2.0;
        const p = Math.max(0, m.userData.pulse);
        m.scale.setScalar(base * (1 + p * 1.1));
        if (m.userData.halo) m.userData.halo.material.opacity = 0.6 + p * 0.5;
        if (p <= 0 && m.userData.halo) m.userData.halo.material.opacity = 0.6;
      } else {
        const b = 1 + Math.sin(t * 1.6 + i * 0.7) * 0.09;
        m.scale.setScalar(base * b);
        if (m.userData.halo) m.userData.halo.material.opacity = 0.42 + Math.sin(t * 1.6 + i * 0.7) * 0.16;
      }
      if (m.userData.pulse <= 0 && m.material.color.getHex() !== it._color && this.hovered !== m) {
        m.material.color.setHex(it._color);
      }
    });

    /* impulsions le long des synapses */
    if (this.pulses && this._items.length) {
      const arr = this.pulseGeo.attributes.position.array;
      const n = this.pulseCount;
      for (let i = 0; i < n; i++) {
        const it = this._items[(i * 13) % this._items.length];
        if (!it || !it._pos) { arr[i * 3] = 0; arr[i * 3 + 1] = 0; arr[i * 3 + 2] = 0; continue; }
        const u = ((t * 0.42 + i * 0.137) % 1);
        const p = it._pos.clone().multiplyScalar(1 - u);
        p.y += Math.sin(Math.PI * u) * 1.6;
        arr[i * 3] = p.x; arr[i * 3 + 1] = p.y; arr[i * 3 + 2] = p.z;
      }
      this.pulseGeo.attributes.position.needsUpdate = true;
    }

    /* picking */
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects(this.nodeMeshes, false);
    this._setHover(hits.length ? hits[0].object : null);

    fitLabelScale(this.camera, this.canvas, this.labels, 14, this.orbit.state.target);
    this.renderer.render(this.scene, this.camera);
  };

  /* ==========================================================================
     B. AGENT LOOP — lemniscate de Gerono (figure-8) en 3D
     ========================================================================== */
  function AgentLoop(canvas) {
    const self = this;
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(DPR());
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
    this.camera.position.set(0, 4, 40);
    /* theta = 0 : la camera regarde l'axe +Z, donc la lemniscate se lit
       comme un vrai "8" a l'ecran. phi faible = leger plongeon 3D.
       sway : balancement doux plutot qu'une rotation continue (une rotation
       continue ferait passer la courbe de profil une fois sur deux). */
    this.orbit = makeOrbit(canvas, this.camera, {
      dist: 40, theta: 0, phi: 0.10, sway: true, swayAmp: 0.40, swaySpeed: 0.30
    });

    this.scene.add(new THREE.AmbientLight(0x24465c, 1.4));
    const l1 = new THREE.PointLight(0x7fe6ff, 1.5, 160); l1.position.set(10, 14, 14);
    const l2 = new THREE.PointLight(0xff9a3c, 1.2, 160); l2.position.set(-12, -8, -10);
    this.scene.add(l1); this.scene.add(l2);

    this.group = new THREE.Group();
    this.scene.add(this.group);

    this._shape = 'loop';
    this._flight = false;
    this._boltLevel = 2;
    this._thick = 1;
    this._speed = 1;
    this._autoSpin = true;
    this._manual = false;
    this._activeStage = 0;
    this._stageCb = null;
    this._head = 0;
    this._zoom = 1;

    this._buildCurve();

    /* Tete de charge + halo */
    this.head = new THREE.Mesh(
      new THREE.SphereGeometry(0.72, 20, 14),
      new THREE.MeshBasicMaterial({ color: 0xffffff })
    );
    this.group.add(this.head);
    this.headHalo = new THREE.Sprite(new THREE.SpriteMaterial({
      map: haloTexture(), color: C.HIT, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    this.headHalo.scale.setScalar(7);
    this.group.add(this.headHalo);

    /* Trainee de la tete */
    const TRAIL = 60;
    const tgeo = new THREE.BufferGeometry();
    tgeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(TRAIL * 3), 3));
    this.trailCount = TRAIL;
    this.trailGeo = tgeo;
    this.trail = new THREE.Line(tgeo, new THREE.LineBasicMaterial({
      color: 0x9fefff, transparent: true, opacity: 0.75,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    this.trail.frustumCulled = false;
    this.group.add(this.trail);

    /* Particules qui parcourent la courbe */
    const N = 420;
    const pgeo = new THREE.BufferGeometry();
    pgeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 3), 3));
    this.particleCount = N;
    this.particleGeo = pgeo;
    this.particles = new THREE.Points(pgeo, new THREE.PointsMaterial({
      color: C.HIT, size: 0.34, transparent: true, opacity: 0.95,
      blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true
    }));
    this.particles.frustumCulled = false;
    this.group.add(this.particles);

    /* Les stations du pipeline sont creees par _buildCurve() (appele plus haut) :
       on ne les recree pas ici, sinon on laisserait un groupe orphelin. */

    /* Etape active : anneau lumineux sur la courbe */
    this.stageRing = new THREE.Mesh(
      new THREE.TorusGeometry(2.1, 0.14, 8, 36),
      new THREE.MeshBasicMaterial({ color: C.SAVED, transparent: true, opacity: 0.9 })
    );
    this.stageRing.raycast = function () {};
    this.group.add(this.stageRing);

    /* --- API publique ---------------------------------------------- */
    this.setShape = function (s) { self._setShape(s); };
    this.setFlight = function (b) { self._flight = !!b; };
    this.setBolts = function (n) { self._setBolts(n); };
    this.setThick = function (t) { self._setThick(t); };
    this.setSpeed = function (s) { self._speed = clamp(+s || 1, 0.2, 4); };
    this.jumpToStage = function (i) {
      self._head = (clamp(i | 0, 0, STAGES.length - 1) + 0.08) / STAGES.length;
    };
    this.onStage = function (cb) { self._stageCb = cb; };
    this.resetOrbit = function () { self.orbit.reset(); self._frameCurve(); };
    /* Une passe complete du pipeline : la tete de charge visite les cinq
       etapes dans l'ordre (PROMPT -> NORMALIZE -> KEY -> CACHE -> SERVE),
       avec une pause sur chacune. C'est le workflow du proxy, anime. */
    this.runPipeline = function () {
      self._runStage = 0;
      self._runHold = 0;
      self._running = true;
    };
    this.isRunning = function () { return !!self._running; };
    this.resize = function () { self._resize(); };

    Object.defineProperty(this, 'shape', { get: () => self._shape });
    Object.defineProperty(this, 'flight', { get: () => self._flight, set: v => { self._flight = !!v; } });
    Object.defineProperty(this, 'boltLevel', { get: () => self._boltLevel });
    Object.defineProperty(this, 'thick', { get: () => self._thick });
    Object.defineProperty(this, 'speed', { get: () => self._speed });
    Object.defineProperty(this, 'autoSpin', {
      get: () => self._autoSpin,
      set: v => {
        self._autoSpin = !!v; self._manual = !v;
        self.orbit.state.auto = !!v; self.orbit.state.manual = !v;
      }
    });
    Object.defineProperty(this, 'manual', {
      get: () => self._manual,
      set: v => { self._manual = !!v; self.orbit.state.manual = !!v; }
    });
    Object.defineProperty(this, 'activeStage', { get: () => self._activeStage });
    Object.defineProperty(this, 'zoom', {
      get: () => self._zoom,
      set: v => { self._zoom = clamp(+v || 1, 0.5, 2.4); }
    });

    this._resize();
    this._frameCurve();
    autoResize(this);
    this._loop = startLoop(this);
  }

  AgentLoop.prototype._resize = function () {
    const cv = this.canvas;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;          // canvas masque : on garde la derniere taille
    this._w = w; this._h = h;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (!this._framed) { this._framed = true; this._frameCurve(); }
  };

  /* Recadre la camera sur la courbe : le "8" remplit le cadre sans etre
     coupe, quelle que soit la forme choisie (lemniscate, tore, helice...). */
  AgentLoop.prototype._frameCurve = function () {
    if (!this.curve) return;
    const box = new THREE.Box3();
    const N = 180;
    for (let i = 0; i <= N; i++) box.expandByPoint(this.curve.pointAt(i / N));
    /* marge pour les stations (anneaux) et les etiquettes d'etape */
    box.min.y -= 2.4; box.max.y += 4.8;
    box.min.x -= 3.0; box.max.x += 3.0;
    box.min.z -= 2.2; box.max.z += 2.2;
    this.orbit.frameBox(box, 1.1);
  };

  /* Formes disponibles pour le pipeline. */
  AgentLoop.prototype._shapeFn = function (shape) {
    if (shape === 'torus') {
      return t => new THREE.Vector3(
        16 * Math.cos(t * Math.PI * 2),
        4.2 * Math.sin(t * Math.PI * 2 * 2),
        16 * Math.sin(t * Math.PI * 2)
      );
    }
    if (shape === 'helix') {
      return t => {
        const a = t * Math.PI * 2 * 2;
        return new THREE.Vector3(
          9 * Math.cos(a),
          (t - 0.5) * 22,
          9 * Math.sin(a) + (Math.floor(t * 4) % 2 ? 7 : -7)
        );
      };
    }
    if (shape === 'trefoil') {
      return t => {
        const a = t * Math.PI * 2;
        return new THREE.Vector3(
          (Math.sin(a) + 2 * Math.sin(2 * a)) * 6.5,
          (Math.cos(a) - 2 * Math.cos(2 * a)) * 6.5,
          Math.sin(3 * a) * 6.5
        );
      };
    }
    /* Lemniscate de Gerono (figure-8) : x = A.cos t, y = B.sin t.cos t.
       Unlike the rational Bernoulli form, its derivative never vanishes, so
       the arc-length table stays perfectly conditioned.
       Proportions ~1.3:1 pour que le 8 se lise dans un cadre panoramique. */
    return t => new THREE.Vector3(
      13 * Math.cos(t * Math.PI * 2),
      20 * Math.sin(t * Math.PI * 2) * Math.cos(t * Math.PI * 2),
      3.6 * Math.sin(t * Math.PI * 4)
    );
  };

  AgentLoop.prototype._buildCurve = function () {
    if (this.tube) {
      this.group.remove(this.tube);
      this.tube.geometry.dispose();
      this.tube.material.dispose();
      this.tube = null;
    }
    this.curve = new SafeCurve(this._shapeFn(this._shape), 1200);
    this._rebuildTube();
    this._buildStations();
    this._buildBolts();
  };

  AgentLoop.prototype._rebuildTube = function () {
    const radius = 0.42 * this._thick;
    /* On echantillonne la courbe (SafeCurve) et on construit un tube a
       section constante : pas de TubeGeometry (evite tout NaN). */
    const pts = [];
    const SEG = 260;
    for (let i = 0; i <= SEG; i++) pts.push(this.curve.pointAt(i / SEG));
    const mat = new THREE.MeshBasicMaterial({
      color: C.HIT, transparent: true, opacity: 0.26,
      blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide
    });
    this.tube = taperedTube(pts, () => radius, 10, mat);
    if (this.tube) {
      this.tube.frustumCulled = false;
      this.group.add(this.tube);
    }

    /* Contour net : deux lignes, une par lobe, pour lire la figure-8. */
    if (!this.lines) this.lines = [];
    this.lines.forEach(l => { this.group.remove(l); l.geometry.dispose(); l.material.dispose(); });
    this.lines = [];
    const lobes = [
      { from: 0.0, to: 0.5, color: 0x8ff4ff },
      { from: 0.5, to: 1.0, color: 0xffb066 }
    ];
    lobes.forEach(lb => {
      const lp = [];
      for (let i = 0; i <= 180; i++) lp.push(this.curve.pointAt(lb.from + (lb.to - lb.from) * (i / 180)));
      const lg = new THREE.BufferGeometry().setFromPoints(lp);
      const line = new THREE.Line(lg, new THREE.LineBasicMaterial({
        color: lb.color, transparent: true, opacity: 0.9,
        blending: THREE.AdditiveBlending, depthWrite: false
      }));
      line.frustumCulled = false;
      this.group.add(line);
      this.lines.push(line);
    });
  };

  /* Stations du pipeline : anneau + faisceau + etiquette a chaque etape. */
  AgentLoop.prototype._buildStations = function () {
    if (this.stageGroup) {
      this.group.remove(this.stageGroup);
      this.stageGroup.traverse(o => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); }
      });
    }
    this.stageGroup = new THREE.Group();
    this.group.add(this.stageGroup);
    this.stageLabels = [];
    this.stageGroup.userData = { rings: [] };

    STAGES.forEach((name, i) => {
      const u = (i + 0.5) / STAGES.length;
      const p = this.curve.pointAt(u);
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(1.55, 0.1, 8, 32),
        new THREE.MeshBasicMaterial({ color: 0x7fe6ff, transparent: true, opacity: 0.55 })
      );
      ring.position.copy(p);
      ring.lookAt(this.camera.position);
      ring.raycast = function () {};
      this.stageGroup.add(ring);
      this.stageGroup.userData.rings.push(ring);

      const beam = new THREE.Mesh(
        new THREE.CylinderGeometry(0.09, 0.09, 3.2, 8),
        new THREE.MeshBasicMaterial({
          color: 0x7fe6ff, transparent: true, opacity: 0.4,
          blending: THREE.AdditiveBlending, depthWrite: false
        })
      );
      beam.position.copy(p).add(new THREE.Vector3(0, 1.6, 0));
      beam.raycast = function () {};
      this.stageGroup.add(beam);

      const lbl = labelSprite(name, 0x9fefff, 0.9);
      lbl.position.copy(p).add(new THREE.Vector3(0, 3.6, 0));
      this.stageGroup.add(lbl);
      this.stageLabels.push(lbl);
    });
  };

  /* Eclairs : lignes brisees entre deux points voisins de la courbe. */
  AgentLoop.prototype._buildBolts = function () {
    if (this.bolts) {
      this.bolts.forEach(b => { this.group.remove(b); b.geometry.dispose(); b.material.dispose(); });
      this.bolts = [];
    }
    this.bolts = [];
    if (this._boltLevel <= 0) return;
    const count = this._boltLevel;
    for (let i = 0; i < count; i++) {
      const base = 0.12 + i * 0.31;
      const pts = [];
      const segs = 10;
      for (let s = 0; s <= segs; s++) {
        const u = (base + s / segs * 0.16) % 1;
        const p = this.curve.pointAt(u);
        const jitter = (s === 0 || s === segs) ? 0 : (Math.random() - 0.5) * 3.2;
        pts.push(new THREE.Vector3(p.x + jitter, p.y + jitter * 0.6, p.z + jitter * 0.4));
      }
      const g = new THREE.BufferGeometry().setFromPoints(pts);
      const l = new THREE.Line(g, new THREE.LineBasicMaterial({
        color: C.SAVED, transparent: true, opacity: 0.95,
        blending: THREE.AdditiveBlending, depthWrite: false
      }));
      l.frustumCulled = false;
      this.group.add(l);
      this.bolts.push(l);
    }
  };

  AgentLoop.prototype._setShape = function (s) {
    if (s === this._shape) return;
    this._shape = s;
    this._buildCurve();
    this._frameCurve();      // chaque forme a son propre gabarit
  };

  AgentLoop.prototype._setBolts = function (n) {
    n = clamp(Math.round(n), 0, 3);
    this._boltLevel = n;
    this._buildBolts();
  };

  AgentLoop.prototype._setThick = function (t) {
    this._thick = clamp(+t || 1, 0.5, 2.2);
    if (this.curve) this._rebuildTube();
  };

  AgentLoop.prototype.step = function (dt, now) {
    const t = now * 0.001;
    this.orbit.update(dt, this._flight ? 0.45 : 0.16);
    if (this._zoom !== 1) {
      this.camera.position.sub(this.orbit.state.target)
        .multiplyScalar(1 + (this._zoom - 1) * 0.03)
        .add(this.orbit.state.target);
    }

    const speed = (this._flight ? 0.30 : 0.085) * this._speed;
    if (this._running) {
      /* Mode pipeline : la tete avance et s'arrete sur chaque etape. */
      this._runHold -= dt;
      if (this._runHold <= 0) {
        this._runStage = (this._runStage + 1) % (STAGES.length + 1);
        if (this._runStage >= STAGES.length) {
          this._running = false;      // tour complet -> retour au libre
        } else {
          this._runHold = 0.85;       // pause sur l'etape
          this._head = (this._runStage + 0.5) / STAGES.length;
          if (this._stageCb) this._stageCb(this._runStage, STAGES[this._runStage]);
        }
      }
    } else {
      this._head = (this._head + dt * speed) % 1;
    }

    const pos = this.curve.pointAt(this._head);
    this.head.position.copy(pos);
    this.headHalo.position.copy(pos);

    /* etape active du pipeline */
    const st = Math.min(STAGES.length - 1, Math.floor(this._head * STAGES.length));
    if (st !== this._activeStage) {
      this._activeStage = st;
      if (this._stageCb) this._stageCb(st, STAGES[st]);
    }
    const sp = this.curve.pointAt((this._activeStage + 0.5) / STAGES.length);
    this.stageRing.position.copy(sp);
    this.stageRing.lookAt(this.camera.position);
    this.stageRing.material.opacity = 0.55 + Math.sin(t * 4) * 0.3;

    /* stations : celle active pulse */
    const rings = this.stageGroup && this.stageGroup.userData && this.stageGroup.userData.rings;
    if (rings) {
      rings.forEach((r, i) => {
        const on = i === this._activeStage;
        r.material.opacity = on ? 0.95 : 0.32;
        r.material.color.setHex(on ? C.SAVED : 0x7fe6ff);
        r.scale.setScalar(on ? 1.25 + Math.sin(t * 5) * 0.12 : 1);
        r.lookAt(this.camera.position);
      });
    }

    /* trainee */
    const tarr = this.trailGeo.attributes.position.array;
    for (let i = 0; i < this.trailCount; i++) {
      const u = (this._head - i * 0.0032 + 1) % 1;
      const p = this.curve.pointAt(u);
      tarr[i * 3] = p.x; tarr[i * 3 + 1] = p.y; tarr[i * 3 + 2] = p.z;
    }
    this.trailGeo.attributes.position.needsUpdate = true;

    /* particules */
    const arr = this.particleGeo.attributes.position.array;
    const N = this.particleCount;
    const flow = this._flight ? 0.55 : 0.16;
    for (let i = 0; i < N; i++) {
      const u = (i / N + t * flow) % 1;
      const p = this.curve.pointAt(u);
      const w = (t * 0.8 + i * 0.13) % 1;
      arr[i * 3] = p.x + Math.sin(w * 12 + i) * 0.3;
      arr[i * 3 + 1] = p.y + Math.cos(w * 9 + i) * 0.3;
      arr[i * 3 + 2] = p.z + Math.sin(w * 11 + i * 0.7) * 0.3;
    }
    this.particleGeo.attributes.position.needsUpdate = true;

    /* la couleur des particules suit le lobe : froid en haut, chaud en bas */
    this.particles.material.color.setHex(this._head < 0.5 ? 0x66f0ff : 0xffa552);

    /* eclairs qui clignotent */
    if (this.bolts) {
      this.bolts.forEach((b, i) => {
        b.material.opacity = 0.3 + 0.7 * Math.abs(Math.sin(t * (3 + i) + i));
      });
    }

    fitLabelScale(this.camera, this.canvas, this.stageLabels, 12, this.orbit.state.target);
    this.renderer.render(this.scene, this.camera);
  };

  /* ==========================================================================
     C. CHART3D — histogramme 3D extrude empile + ruban des tokens economises
     ========================================================================== */
  function Chart3D(canvas, opts) {
    const self = this;
    this.canvas = canvas;
    this.opts = Object.assign({
      maxBars: 40, showFloor: true, savedLine: true, labelMode: 'time',
      maxBarW: 0.28, autoSpin: false, showLabels: true, showGrid: true,
      segNames: ['hits', 'misses', 'bypass'],
      segLabels: ['Cache hits', 'Misses (paid)', 'Bypass'],
      colorMap: { hits: C.HIT, misses: C.MISS, bypass: C.BYPASS },
      savedColor: C.SAVED
    }, opts || {});
    if (opts && opts.colorMap) {
      this.opts.colorMap = Object.assign(
        { hits: C.HIT, misses: C.MISS, bypass: C.BYPASS }, opts.colorMap);
    }

    this.renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(DPR());
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 1000);
    /* Angle classique d'un histogramme 3D : legere vue de trois-quarts.
       sway : balancement doux plutot qu'une rotation continue — une rotation
       continue ferait tourner l'axe des abscisses sous les yeux. */
    this.orbit = makeOrbit(canvas, this.camera, {
      dist: 46, theta: 0.45, phi: 0.24, sway: true, swayAmp: 0.26, swaySpeed: 0.16
    });
    /* Un graphique ne tourne pas tout seul (autoSpin = false par defaut). */
    this.orbit.state.auto = !!this.opts.autoSpin;

    this.scene.add(new THREE.AmbientLight(0x2b4a60, 1.5));
    const l1 = new THREE.PointLight(0x9fefff, 1.4, 200); l1.position.set(20, 30, 25);
    const l2 = new THREE.PointLight(0xff9a4a, 0.9, 200); l2.position.set(-25, 10, -20);
    this.scene.add(l1); this.scene.add(l2);

    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.barsGroup = new THREE.Group();
    this.floorGroup = new THREE.Group();
    this.labelsGroup = new THREE.Group();
    this.group.add(this.barsGroup);
    this.group.add(this.floorGroup);
    this.group.add(this.labelsGroup);

    this._points = [];
    this._hoverCb = null;
    this._hoverIndex = -1;
    this._zoom = 1;

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2(-10, -10);
    this.pickables = [];

    canvas.addEventListener('pointermove', e => {
      const r = canvas.getBoundingClientRect();
      self.pointer.x = ((e.clientX - r.left) / r.width) * 2 - 1;
      self.pointer.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    });
    canvas.addEventListener('pointerleave', () => { self.pointer.set(-10, -10); });

    this.setData = function (pts) { self._setData(pts || []); };
    this.onHover = function (cb) { self._hoverCb = cb; };
    this.resize = function () { self._resize(); };
    this.resetOrbit = function () { self.orbit.reset(); self._frameData(); };
    /* Amene la camera sur le baton le plus haut : on lit le pic sans
       avoir a parcourir toute la serie. */
    this.focusPeak = function () {
      let best = null, bestVal = -1;
      self.pickables.forEach(m => {
        const tot = (m.userData.point.hits || 0) + (m.userData.point.misses || 0) + (m.userData.point.bypass || 0);
        if (tot > bestVal) { bestVal = tot; best = m; }
      });
      if (!best) return null;
      self.orbit.focus(best.position.clone(), Math.max(16, self.orbit.state.dist * 0.45));
      return best.userData.point;
    };
    this.setOptions = function (o) { Object.assign(self.opts, o || {}); self._setData(self._points); };

    Object.defineProperty(this, 'autoSpin', {
      get: () => self.opts.autoSpin,
      set: v => { self.opts.autoSpin = !!v; self.orbit.state.auto = !!v; }
    });
    Object.defineProperty(this, 'showLabels', {
      get: () => self.opts.showLabels,
      set: v => { self.opts.showLabels = !!v; self.labelsGroup.visible = !!v; }
    });
    Object.defineProperty(this, 'showGrid', {
      get: () => self.opts.showGrid,
      set: v => { self.opts.showGrid = !!v; self.floorGroup.visible = !!v; }
    });
    Object.defineProperty(this, 'savedLine', {
      get: () => self.opts.savedLine,
      set: v => { self.opts.savedLine = !!v; if (self.savedRibbon) self.savedRibbon.visible = !!v; }
    });
    Object.defineProperty(this, 'zoom', {
      get: () => self._zoom,
      set: v => { self._zoom = clamp(+v || 1, 0.5, 2.4); }
    });

    this._resize();
    autoResize(this);
    this._loop = startLoop(this);
  }

  Chart3D.prototype._resize = function () {
    const cv = this.canvas;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;          // canvas masque : on garde la derniere taille
    this._w = w; this._h = h;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (!this._framed) { this._framed = true; this._frameData(); }
  };

  /* Barrique a gradient vertical : sombre en bas, saturé en haut. */
  Chart3D.prototype._barGeometry = function (bw, h) {
    const g = new THREE.BoxGeometry(bw, h, bw);
    const pos = g.attributes.position;
    const colors = new Float32Array(pos.count * 3);
    for (let i = 0; i < pos.count; i++) {
      const y = pos.getY(i);
      const f = clamp((y + h / 2) / (h || 1), 0, 1);
      colors[i * 3] = f; colors[i * 3 + 1] = f; colors[i * 3 + 2] = f;
    }
    g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    return g;
  };

  /* Etendue horizontale de la scene. Avec peu de barres on resserre la
     scene (sinon une barre unique flotte au milieu d'un sol immense) ; avec
     beaucoup de barres on garde le pas regulier. */
  Chart3D.prototype._spanFor = function (n) {
    if (n <= 4) return Math.max(3.2, n * 2.4);
    return Math.max(8, n * 1.4);
  };

  Chart3D.prototype._setData = function (pts) {
    this._points = pts;
    this.pickables = [];

    const purge = (grp) => {
      while (grp.children.length) {
        const o = grp.children[0];
        grp.remove(o);
        if (o.geometry) o.geometry.dispose();
        if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); }
      }
    };
    purge(this.barsGroup);
    purge(this.floorGroup);
    purge(this.labelsGroup);

    const data = pts.slice(-this.opts.maxBars);
    const n = data.length;
    if (!n) { this._maxVal = 1; this._maxSaved = 1; return; }

    let maxVal = 0, maxSaved = 0;
    data.forEach(p => {
      const tot = (p.hits || 0) + (p.misses || 0) + (p.bypass || 0);
      if (tot > maxVal) maxVal = tot;
      if ((p.saved || 0) > maxSaved) maxSaved = p.saved || 0;
    });
    this._maxVal = Math.max(1, maxVal);
    this._maxSaved = Math.max(1, maxSaved);

    const span = this._spanFor(n);
    const step = span / n;
    const bw = Math.min(this.opts.maxBarW * 2.6, step * 0.72);
    const H = 11;

    const segKeys = this.opts.segNames;
    const segColors = segKeys.map(k => this.opts.colorMap[k]);
    const savedPts = [];

    data.forEach((p, i) => {
      const x = -span / 2 + step * (i + 0.5);
      let y = 0;
      segKeys.forEach((k, si) => {
        const v = p[k] || 0;
        if (v <= 0) return;
        const h = Math.max(0.08, (v / this._maxVal) * H);
        const base = new THREE.Color(segColors[si]);
        const mat = new THREE.MeshPhongMaterial({
          color: base, emissive: base.clone().multiplyScalar(0.35),
          shininess: 90, specular: 0xffffff,
          vertexColors: true, transparent: true, opacity: 0.95
        });
        const m = new THREE.Mesh(this._barGeometry(bw, h), mat);
        m.position.set(x, y + h / 2, 0);
        m.userData = { index: i, point: p, seg: k, top: y + h };
        this.barsGroup.add(m);
        this.pickables.push(m);
        y += h;
      });

      /* Capuchon lumineux au sommet du baton */
      const cap = new THREE.Mesh(
        new THREE.BoxGeometry(bw * 1.14, 0.09, bw * 1.14),
        new THREE.MeshBasicMaterial({
          color: 0xffffff, transparent: true, opacity: 0.75,
          blending: THREE.AdditiveBlending, depthWrite: false
        })
      );
      cap.position.set(x, y + 0.05, 0);
      cap.raycast = function () {};
      this.barsGroup.add(cap);

      if (this.opts.savedLine) {
        const sh = (p.saved || 0) / this._maxSaved * H * 0.92;
        savedPts.push(new THREE.Vector3(x, sh, 0));
      }
    });

    /* Ruban 3D des tokens economises */
    if (this.savedRibbon) {
      this.group.remove(this.savedRibbon);
      this.savedRibbon.geometry.dispose();
      this.savedRibbon.material.dispose();
      this.savedRibbon = null;
    }
    if (this.savedDots) {
      this.group.remove(this.savedDots);
      this.savedDots.geometry.dispose();
      this.savedDots.material.dispose();
      this.savedDots = null;
    }
    if (this.opts.savedLine && savedPts.length > 1) {
      const mat = new THREE.MeshBasicMaterial({
        color: this.opts.savedColor, transparent: true, opacity: 0.75,
        blending: THREE.AdditiveBlending, depthWrite: false
      });
      const rib = taperedTube(savedPts, () => 0.14, 6, mat);
      if (rib) {
        rib.frustumCulled = false;
        this.group.add(rib);
        this.savedRibbon = rib;
      }
      /* points lumineux le long du ruban */
      const sg = new THREE.BufferGeometry().setFromPoints(savedPts);
      const dots = new THREE.Points(sg, new THREE.PointsMaterial({
        color: this.opts.savedColor, size: 0.42, transparent: true, opacity: 0.95,
        blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true
      }));
      dots.frustumCulled = false;
      this.group.add(dots);
      this.savedDots = dots;
    }

    /* Sol + grille */
    if (this.opts.showFloor) {
      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(span + 8, span * 0.6),
        new THREE.MeshBasicMaterial({
          color: 0x061019, transparent: true, opacity: 0.72, side: THREE.DoubleSide
        })
      );
      plane.rotation.x = -Math.PI / 2;
      plane.position.y = -0.04;
      this.floorGroup.add(plane);

      const stepG = span / 12;
      for (let i = 0; i <= 12; i++) {
        const g = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(-span / 2 + i * stepG, 0, -span * 0.28),
          new THREE.Vector3(-span / 2 + i * stepG, 0, span * 0.28)
        ]);
        this.floorGroup.add(new THREE.Line(g, new THREE.LineBasicMaterial({
          color: C.GRID, transparent: true, opacity: 0.34
        })));
      }
      for (let j = 0; j <= 5; j++) {
        const z = -span * 0.28 + (j / 5) * span * 0.56;
        const g = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(-span / 2, 0, z), new THREE.Vector3(span / 2, 0, z)
        ]);
        this.floorGroup.add(new THREE.Line(g, new THREE.LineBasicMaterial({
          color: C.GRID, transparent: true, opacity: 0.2
        })));
      }
    }

    /* Etiquettes de l'axe X (temps ou modele) */
    const labelEvery = Math.max(1, Math.ceil(n / 9));
    data.forEach((p, i) => {
      if (i % labelEvery !== 0 && i !== n - 1) return;
      const raw = String(p.t || '');
      const txt = this.opts.labelMode === 'raw' ? raw.slice(0, 22) : raw.slice(5, 16);
      if (!txt) return;
      const sp = labelSprite(txt, 0x9fefff, 0.9);
      sp.position.set(-span / 2 + step * (i + 0.5), -1.6, span * 0.22);
      this.labelsGroup.add(sp);
    });

    /* Valeurs affichees au sommet des batons les plus hauts : on lit le
       chiffre sans avoir a survoler. */
    const ranked = data.map((p, i) => {
      let tot = 0;
      this.opts.segNames.forEach(k => { tot += (p[k] || 0); });
      return { i: i, tot: tot };
    }).filter(r => r.tot > 0).sort((a, b) => b.tot - a.tot);
    ranked.slice(0, 4).forEach(r => {
      const x = -span / 2 + step * (r.i + 0.5);
      let yy = 0;
      data[r.i] && this.opts.segNames.forEach(k => {
        const v = data[r.i][k] || 0;
        if (v > 0) yy += (v / this._maxVal) * H;
      });
      const sp = labelSprite(String(r.tot), 0xffffff, 0.9);
      sp.position.set(x, yy + 1.6, 0);
      sp.userData.isValue = true;
      this.labelsGroup.add(sp);
    });

    /* Axe des valeurs (graduations a gauche du sol). */
    for (let k = 0; k <= 2; k++) {
      const v = Math.round((this._maxVal * (2 - k)) / 2);
      const sp = labelSprite(String(v), 0x7fb4d8, 0.9);
      sp.position.set(-span / 2 - 1.6, (H * (2 - k)) / 2, -span * 0.3);
      this.labelsGroup.add(sp);
    }

    /* cadrage : on encadre exactement le volume occupe par les donnees */
    this._frameData();
  };

  /* Recadre la camera sur le volume des barres : le graphique remplit le
     canvas au lieu de flotter au centre d'un grand vide. */
  Chart3D.prototype._frameData = function () {
    const data = this._points.slice(-this.opts.maxBars);
    const n = data.length;
    if (!n) return;
    const span = this._spanFor(n);
    const step = span / n;
    const H = 11;
    const pts = [];
    data.forEach((p, i) => {
      const x = -span / 2 + step * (i + 0.5);
      let tot = 0;
      this.opts.segNames.forEach(k => { tot += (p[k] || 0); });
      const h = (tot / this._maxVal) * H;
      pts.push(new THREE.Vector3(x, -2.0, 0));            // ligne d'etiquette
      pts.push(new THREE.Vector3(x, Math.max(h, 0) + 1.4, 0));  // sommet du baton
      if (this.opts.savedLine && (p.saved || 0)) {
        pts.push(new THREE.Vector3(x, (p.saved / this._maxSaved) * H * 0.92 + 1.2, 0));
      }
    });
    this.orbit.framePoints(pts, 1.06);
  };

  Chart3D.prototype.step = function (dt, now) {
    this.orbit.update(dt, 0.05);
    if (this._zoom !== 1) {
      this.camera.position.sub(this.orbit.state.target)
        .multiplyScalar(1 + (this._zoom - 1) * 0.02)
        .add(this.orbit.state.target);
    }

    /* survol */
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects(this.pickables, false);
    const idx = hits.length ? hits[0].object.userData.index : -1;
    if (idx !== this._hoverIndex) {
      this._hoverIndex = idx;
      if (this._hoverCb) {
        const m = idx >= 0 ? this.pickables.find(x => x.userData.index === idx) : null;
        this._hoverCb(m ? m.userData.point : null);
      }
    }

    fitLabelScale(this.camera, this.canvas, this.labelsGroup.children, 11, this.orbit.state.target);
    this.renderer.render(this.scene, this.camera);
  };

  /* ------------------------------------------------------------- export */
  window.IcyWebGL = {
    version: '3.0',
    COLORS: C,
    MODEL_COLORS: MODEL_COLORS,
    STAGES: STAGES,
    SHAPES: SHAPES,
    neuron: function (canvas) { return new HumanNeuron(canvas); },
    loop: function (canvas) { return new AgentLoop(canvas); },
    chart: function (canvas, opts) { return new Chart3D(canvas, opts); }
  };
})();
