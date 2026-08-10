/* Service Worker：只求「裝得起來、斷線時還開得了」，不做積極快取。

   這個網站幾乎每個功能都要連網（GitHub API、Groq、逐字稿檔案），離線能做的事本來就少；
   而且這個專案吃過大虧——iOS Safari 抓著舊頁面不放，害修好的東西傳不到手機上，
   所以才有版本自動比對那套。快取策略如果做成「快取優先」，等於把那個問題放大十倍。

   因此一律「網路優先」：連得上就用最新的，順手存一份；連不上才拿出舊的頂著。
   快取名稱綁註冊時帶的 ?v= 版本，換版本就自動丟掉舊快取。 */

var VER = new URL(self.location.href).searchParams.get('v') || 'dev';
var CACHE = 'deck-' + VER;

// 這些是「資料」不是「殼」，永遠走網路，存下來只會拿到過期的東西
var NEVER = [
  'api.github.com',
  'api.groq.com',
  'raw.githubusercontent.com',
  'googleapis.com',
  'pipedapi',
  'i.ytimg.com'
];

self.addEventListener('install', function (e) {
  self.skipWaiting();                       // 新版立刻接手，不用等所有分頁關掉
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
      // 只預存最低限度：外殼與圖示。失敗也不要擋住安裝。
      return c.addAll(['./', './index.html', './manifest.webmanifest',
                       './assets/icon-192.png', './assets/icon-512.png'])
              .catch(function () {});
    })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.map(function (n) {
        if (n !== CACHE) return caches.delete(n);   // 換版本就清掉舊快取
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;          // 跨網域的交給瀏覽器自己處理
  if (NEVER.some(function (h) { return url.href.indexOf(h) > -1; })) return;

  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok && res.type === 'basic') {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); });
      }
      return res;
    }).catch(function () {
      // 只有真的連不上才拿舊的出來；首頁另外退回 index.html，
      // 這樣從主畫面圖示開啟時就算沒網路也不會是一片空白。
      return caches.match(req).then(function (hit) {
        return hit || (req.mode === 'navigate' ? caches.match('./index.html') : undefined);
      });
    })
  );
});
