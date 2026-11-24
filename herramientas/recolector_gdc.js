// Recolector de circuitos MT para visores SPARD GD-Connect (EBSA, ENERCA, EMSA).
// Se pega en la consola del visor DESPUÉS de entrar normalmente (política de datos y captcha resueltos por el usuario).
// Usa las mismas funciones del visor (execute_new), una consulta cada 10 s, y se detiene tras 4 fallos seguidos.
//   __instalar("emsaTrabajo", "Visor EMSA GD-Connect", [códigos ya importados])
//   __agregar("emsa_circuitos_pasada1.json", "Meta: cabeceras y veredas", [[lon, lat, mun], ...])
//   __agregar("ebsa_codigos.json", "Barrido por código", [13500, 13501, ...])  códigos de circuito en vez de puntos
//   __trabajar()      inicia o retoma (el avance queda en localStorage tras cada punto)
//   __resumen()       estado de cada grupo
//   __detener()       para el recorrido al terminar el punto en curso
//   __bajar(i)        descarga el grupo i (también queda un enlace naranja arriba a la izquierda)
//   __instalar(LSK, FUENTE, PREVIOS, { todos: true })  consulta todos los puntos aunque ya estén cerca de un circuito
//                     recolectado (para zonas densas donde varios circuitos corren juntos, p. ej. Paipa–Duitama)
window.__instalar = (LSK, FUENTE, PREVIOS, OPC = {}) => {
  const CEL = 0.005, PAUSA = 10000;
  // Las pausas corren en un Worker: Chrome frena los temporizadores de las pestañas en segundo plano
  // (hasta 1 por minuto) y el recorrido tardaría 7 veces más. Si el visor no deja crear el Worker, setTimeout.
  const reloj = (() => {
    try {
      const w = new Worker(URL.createObjectURL(new Blob(["onmessage=e=>setTimeout(()=>postMessage(e.data[0]),e.data[1])"])));
      const pend = {}; let k = 0, vivo = true;
      w.onmessage = e => { const f = pend[e.data]; delete pend[e.data]; f && f(); };
      // el visor de EBSA bloquea los Worker (política CSP): el error llega después de crearlo, así que
      // las pausas pendientes y las siguientes pasan a setTimeout
      w.onerror = () => { vivo = false; Object.keys(pend).forEach(i => { const [f, ms] = pend[i]; delete pend[i]; setTimeout(f, ms); }); };
      w.onmessage = e => { const p = pend[e.data]; delete pend[e.data]; p && p[0](); };
      return ms => new Promise(r => { if (!vivo) return setTimeout(r, ms); pend[++k] = [r, ms]; w.postMessage([k, ms]); });
    } catch (e) { return null; }
  })();
  const esp = reloj || (ms => new Promise(r => setTimeout(r, ms)));
  const T = JSON.parse(localStorage.getItem(LSK) || '{"grupos":[]}');
  const guardar = () => localStorage.setItem(LSK, JSON.stringify(T));
  const previos = new Set((PREVIOS || []).map(String)), cub = new Set();
  const marca = (lon, lat) => cub.add(Math.floor(lon / CEL) + "," + Math.floor(lat / CEL));
  const cubierto = (lon, lat) => {
    const i = Math.floor(lon / CEL), j = Math.floor(lat / CEL);
    for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) if (cub.has((i + a) + "," + (j + b))) return true;
    return false;
  };
  T.grupos.forEach(g => Object.values(g.circ).forEach(c => c.tramos.forEach(s => { marca(s[0], s[1]); marca(s[2], s[3]); })));
  const llamar = (metodo, args) => new Promise(res => {
    let fin = false;
    const t = setTimeout(() => { if (!fin) { fin = true; res(null); } }, 60000);
    try {
      execute_new(MODEL_SOLICITUD, metodo, args, true, r => {
        if (fin) return;
        fin = true; clearTimeout(t);
        if (r === false || r == null) return res(null);
        try { r = typeof r === "string" ? JSON.parse(r) : r; res(Array.isArray(r) ? r : []); } catch (e) { res(null); }
      });
    } catch (e) { clearTimeout(t); fin = true; res(null); }
  });
  const E = window.__estado = { corriendo: false, fallos: 0, msg: "instalado", consultas: 0, detener: false, reloj: reloj ? "worker" : "setTimeout" };
  window.__detener = () => { E.detener = true; return "se detiene al terminar el punto actual"; };
  window.__agregar = (archivo, zonas, sem) => { T.grupos.push({ archivo, zonas, idx: 0, circ: {}, sem }); guardar(); return T.grupos.length; };
  window.__limpiarHechos = () => { T.grupos = T.grupos.filter(g => !g.hecho); guardar(); return T.grupos.length; };
  window.__resumen = () => T.grupos.map((g, i) => `${i} ${g.archivo}: ${g.idx}/${g.sem.length} · ${Object.keys(g.circ).length} circuitos${g.hecho ? " · hecho" : ""}`).join("\n")
    + `\n${E.corriendo ? "corriendo" : "detenido"} · ${E.msg} · ${E.consultas} consultas`;
  window.__bajar = i => {
    const g = T.grupos[i];
    const datos = { fuente: FUENTE, fecha: new Date().toISOString().slice(0, 10), zonas: g.zonas, circuitos: g.circ };
    let a = document.getElementById("__btnG");
    if (!a) {
      a = document.createElement("a"); a.id = "__btnG";
      a.style.cssText = "position:fixed;left:10px;top:150px;z-index:99999;background:#f59e0b;color:#111;padding:10px 14px;border-radius:8px;font:600 14px sans-serif;text-decoration:none";
      document.body.appendChild(a);
    }
    a.href = URL.createObjectURL(new Blob([JSON.stringify(datos)], { type: "application/json" }));
    a.download = g.hecho ? g.archivo : g.archivo.replace(".json", "_parcial.json");
    a.textContent = "Descargar " + a.download;
    a.click();
    return a.download;
  };
  window.__trabajar = async () => {
    if (E.corriendo) return "ya corre";
    E.corriendo = true; E.fallos = 0; E.msg = "trabajando";
    const fallo = async donde => {
      if (++E.fallos >= 4) { E.corriendo = false; E.msg = "detenido tras 4 fallos seguidos en " + donde; return true; }
      await esp(PAUSA * 3); return false;
    };
    for (let gi = 0; gi < T.grupos.length; gi++) {
      const g = T.grupos[gi];
      if (g.hecho) continue;
      while (g.idx < g.sem.length) {
        if (E.detener) { E.detener = false; E.corriendo = false; E.msg = "detenido a pedido en " + g.archivo + " punto " + g.idx; return E.msg; }
        if (!Array.isArray(g.sem[g.idx])) {
          // barrido por código de circuito (#52): get_line directo; un código inexistente devuelve una lista vacía
          const c = String(g.sem[g.idx]);
          if (!(previos.has(c) || T.grupos.some(x => x.circ[c]))) {
            const L = await llamar("get_line", { Parents: c, tipo: 4 });
            E.consultas++;
            if (L === null) { if (await fallo(`${g.archivo} código ${c}`)) return E.msg; continue; }
            E.fallos = 0;
            const tr = L.map(s => [+s.lon1, +s.lat1, +s.lon2, +s.lat2].map(v => Math.round(v * 1e6) / 1e6)).filter(s => s.every(Number.isFinite));
            if (tr.length) {
              const m = tr[Math.floor(tr.length / 2)];
              g.circ[c] = { mun: "", semilla: [m[0], m[1]], tramos: tr };
              tr.forEach(s => { marca(s[0], s[1]); marca(s[2], s[3]); });
            }
            await esp(PAUSA);
          }
          g.idx++; guardar(); continue;
        }
        const [lon, lat, mun] = g.sem[g.idx];
        if (OPC.todos || !cubierto(lon, lat)) {
          const r = await llamar("get_map_search", { latitud: lat, longitud: lon, tipo: 0, busquedaExacta: false });
          E.consultas++;
          if (r === null) { if (await fallo(`${g.archivo} punto ${g.idx}`)) return E.msg; continue; }
          E.fallos = 0;
          const cods = [...new Set(r.map(t => String(t.fparent == null ? "" : t.fparent).trim()).filter(Boolean))];
          let repetir = false;
          for (const c of cods) {
            if (previos.has(c) || T.grupos.some(x => x.circ[c])) continue;
            await esp(PAUSA);
            const L = await llamar("get_line", { Parents: c, tipo: 4 });
            E.consultas++;
            if (L === null) { if (await fallo(`${g.archivo} circuito ${c}`)) return E.msg; repetir = true; break; }
            E.fallos = 0;
            const tr = L.map(s => [+s.lon1, +s.lat1, +s.lon2, +s.lat2].map(v => Math.round(v * 1e6) / 1e6)).filter(s => s.every(Number.isFinite));
            g.circ[c] = { mun, semilla: [lon, lat], tramos: tr };
            tr.forEach(s => { marca(s[0], s[1]); marca(s[2], s[3]); });
            guardar();
          }
          if (repetir) continue;
          await esp(PAUSA);
        }
        g.idx++; guardar();
      }
      g.hecho = true; guardar(); window.__bajar(gi);
    }
    E.corriendo = false; E.msg = "terminado";
    return E.msg;
  };
  return window.__resumen();
};
