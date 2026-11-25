const C="pulse-shell-v2",S=["/","/static/manifest.json","/static/icon.svg"];
self.addEventListener("install",e=>e.waitUntil(caches.open(C).then(c=>c.addAll(S))));
self.addEventListener("fetch",e=>{const u=new URL(e.request.url);
 if(u.pathname.startsWith("/api/"))return;
 e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))});
