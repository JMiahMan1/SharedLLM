"""The two self-contained documents the studio can write: a deck and a game.

Both are plain strings with ``__TOKEN__`` placeholders (not ``str.format``): the
CSS and JavaScript are full of braces, and escaping them for a format call is how
a stylesheet ends up on the slide. ``render`` swaps the tokens in one pass.

Neither document fetches anything: no CDN, no fonts, no scripts. A deck opens
from a workspace on a phone with no signal, and a game runs from the sandbox
without a build step -- which is the whole point of putting them in a workspace.
"""

DECK_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: #0b1020; color: #e7ecf5;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
body { display: flex; flex-direction: column; overflow: hidden; }
header { padding: 14px 22px; border-bottom: 1px solid rgba(255,255,255,.08);
  display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
header h2 { margin: 0; font-size: 15px; letter-spacing: .08em; text-transform: uppercase; color: #93a4c4; }
header .count { font-size: 12px; color: #6c7d9e; }
main { flex: 1; position: relative; }
.slide { position: absolute; inset: 0; padding: 6vh 8vw; display: none;
  flex-direction: column; gap: 22px; overflow-y: auto; }
.slide.active { display: flex; }
.slide h1 { font-size: clamp(26px, 4.2vw, 56px); line-height: 1.1; margin: 0; letter-spacing: -.02em; }
.slide ul { margin: 0; padding-left: 22px; display: flex; flex-direction: column; gap: 12px;
  font-size: clamp(16px, 1.8vw, 26px); color: #c8d3e6; }
.slide figure { margin: 0; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.slide img { max-width: min(100%, 900px); max-height: 52vh; border-radius: 12px;
  border: 1px solid rgba(255,255,255,.08); }
.slide figcaption { font-size: 13px; color: #8ea0c0; }
.slide .notes { display: none; }
footer { padding: 10px 22px; border-top: 1px solid rgba(255,255,255,.08);
  display: flex; justify-content: space-between; font-size: 12px; color: #6c7d9e; }
nav { display: flex; gap: 8px; }
nav button { background: rgba(255,255,255,.06); color: #dbe4f3; border: 1px solid rgba(255,255,255,.12);
  border-radius: 10px; padding: 8px 14px; font-size: 13px; cursor: pointer; }
nav button:disabled { opacity: .35; cursor: default; }
@media print {
  body { background: #fff; color: #111; }
  header, footer, nav { display: none; }
  .slide { position: static; display: flex !important; page-break-after: always; padding: 24px; }
  .slide ul { color: #222; }
}
"""

DECK_JS = """
(function () {
  var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
  var index = 0;
  var counter = document.getElementById('count');
  var prev = document.getElementById('prev');
  var next = document.getElementById('next');
  function show(n) {
    index = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach(function (slide, i) { slide.classList.toggle('active', i === index); });
    if (counter) { counter.textContent = (index + 1) + ' / ' + slides.length; }
    if (prev) { prev.disabled = index === 0; }
    if (next) { next.disabled = index === slides.length - 1; }
    if (location.hash !== '#' + (index + 1)) { history.replaceState(null, '', '#' + (index + 1)); }
  }
  document.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowRight' || event.key === 'PageDown' || event.key === ' ') { show(index + 1); }
    if (event.key === 'ArrowLeft' || event.key === 'PageUp') { show(index - 1); }
    if (event.key === 'Home') { show(0); }
    if (event.key === 'End') { show(slides.length - 1); }
  });
  var startX = null;
  document.addEventListener('touchstart', function (e) { startX = e.touches[0].clientX; }, { passive: true });
  document.addEventListener('touchend', function (e) {
    if (startX === null) { return; }
    var dx = e.changedTouches[0].clientX - startX;
    if (Math.abs(dx) > 40) { show(index + (dx < 0 ? 1 : -1)); }
    startX = null;
  }, { passive: true });
  if (prev) { prev.addEventListener('click', function () { show(index - 1); }); }
  if (next) { next.addEventListener('click', function () { show(index + 1); }); }
  var fromHash = parseInt((location.hash || '').replace('#', ''), 10);
  show(isNaN(fromHash) ? 0 : fromHash - 1);
})();
"""

DECK_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<header>
  <h2>__TITLE__</h2>
  <span class="count" id="count">1 / __COUNT__</span>
</header>
<main>
__SLIDES__
</main>
<footer>
  <span>__FOOTER__</span>
  <nav>
    <button id="prev" type="button" aria-label="Previous slide">&larr;</button>
    <button id="next" type="button" aria-label="Next slide">&rarr;</button>
  </nav>
</footer>
<script>__JS__</script>
</body>
</html>
"""


def render(template: str, **values: str) -> str:
    """Replace ``__TOKEN__`` placeholders in one pass (no brace escaping)."""
    out = template
    for key, value in values.items():
        out = out.replace(f"__{key.upper()}__", str(value))
    return out


#: A playable canvas game with no dependencies: keyboard, touch, score, lives and
#: optional sprites. It is a *starting point* a person or Raven can edit, not a
#: framework -- one file, no build step, runs from the sandbox.
GAME_JS = """
(function () {
  var canvas = document.getElementById('game');
  var ctx = canvas.getContext('2d');
  var title = document.body.dataset.title || 'Jarvis Game';
  var W = 480, H = 640;
  var state = 'ready';
  var score = 0, lives = 3, best = 0;
  var player = { x: W / 2, y: H - 60, r: 22, vx: 0 };
  var falling = [];
  var keys = {};
  var spawnAt = 0;
  var sprites = { player: null, star: null };
  var wanted = JSON.parse(document.getElementById('sprites').textContent || '{}');
  function load(name, src) {
    if (!src) { return; }
    var image = new Image();
    image.onload = function () { sprites[name] = image; };
    image.src = src;
  }
  load('player', wanted.player);
  load('star', wanted.star);

  function reset() { score = 0; lives = 3; falling = []; spawnAt = 0; player.x = W / 2; state = 'play'; }

  function spawn() {
    falling.push({ x: 30 + Math.random() * (W - 60), y: -20, r: 14 + Math.random() * 10,
      vy: 90 + Math.random() * 120 + score * 0.6, spin: Math.random() * Math.PI });
  }

  function step(dt) {
    if (state !== 'play') { return; }
    if (keys.left) { player.vx -= 1400 * dt; }
    if (keys.right) { player.vx += 1400 * dt; }
    player.vx *= 0.86;
    player.x = Math.max(player.r, Math.min(W - player.r, player.x + player.vx * dt));
    spawnAt -= dt;
    if (spawnAt <= 0) { spawn(); spawnAt = Math.max(0.25, 0.9 - score * 0.01); }
    for (var i = falling.length - 1; i >= 0; i -= 1) {
      var item = falling[i];
      item.y += item.vy * dt;
      item.spin += dt * 2;
      var dx = item.x - player.x, dy = item.y - player.y;
      if (Math.sqrt(dx * dx + dy * dy) < item.r + player.r) { score += 1; falling.splice(i, 1); continue; }
      if (item.y > H + 30) {
        falling.splice(i, 1); lives -= 1;
        if (lives <= 0) { state = 'over'; best = Math.max(best, score); }
      }
    }
  }

  function drawStar(item) {
    ctx.save();
    ctx.translate(item.x, item.y);
    ctx.rotate(item.spin);
    ctx.fillStyle = '#f7c948';
    ctx.beginPath();
    for (var p = 0; p < 5; p += 1) {
      var angle = (p / 5) * Math.PI * 2 - Math.PI / 2;
      ctx.lineTo(Math.cos(angle) * item.r, Math.sin(angle) * item.r);
      var inner = angle + Math.PI / 5;
      ctx.lineTo(Math.cos(inner) * item.r * 0.45, Math.sin(inner) * item.r * 0.45);
    }
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function draw() {
    ctx.fillStyle = '#0b1020';
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = 'rgba(255,255,255,.05)';
    for (var y = 0; y < H; y += 40) { ctx.fillRect(0, y, W, 1); }
    falling.forEach(function (item) {
      if (sprites.star) {
        ctx.drawImage(sprites.star, item.x - item.r, item.y - item.r, item.r * 2, item.r * 2);
      } else { drawStar(item); }
    });
    if (sprites.player) {
      ctx.drawImage(sprites.player, player.x - player.r, player.y - player.r, player.r * 2, player.r * 2);
    } else {
      ctx.fillStyle = '#6ea8fe';
      ctx.beginPath();
      ctx.arc(player.x, player.y, player.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = '#e7ecf5';
    ctx.font = 'bold 18px ui-sans-serif, system-ui, sans-serif';
    ctx.fillText('Score ' + score, 16, 30);
    ctx.fillText('Lives ' + Math.max(0, lives), W - 96, 30);
    if (state !== 'play') {
      ctx.fillStyle = 'rgba(4,8,18,.78)';
      ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = '#e7ecf5';
      ctx.textAlign = 'center';
      ctx.font = 'bold 30px ui-sans-serif, system-ui, sans-serif';
      ctx.fillText(state === 'ready' ? title : 'Game over', W / 2, H / 2 - 30);
      ctx.font = '16px ui-sans-serif, system-ui, sans-serif';
      ctx.fillText(
        state === 'ready' ? 'Press space or tap to start' : 'Score ' + score + '  ·  best ' + best,
        W / 2, H / 2 + 6
      );
      ctx.fillText(
        state === 'ready' ? 'Catch the falling stars' : 'Press space or tap to play again',
        W / 2, H / 2 + 34
      );
      ctx.textAlign = 'left';
    }
  }

  var last = 0;
  function frame(now) {
    var dt = Math.min(0.05, (now - last) / 1000 || 0);
    last = now;
    step(dt);
    draw();
    requestAnimationFrame(frame);
  }

  function confirmKey() { if (state !== 'play') { reset(); } }

  window.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowLeft' || event.key === 'a') { keys.left = true; }
    if (event.key === 'ArrowRight' || event.key === 'd') { keys.right = true; }
    if (event.key === ' ' || event.key === 'Enter') { confirmKey(); event.preventDefault(); }
  });
  window.addEventListener('keyup', function (event) {
    if (event.key === 'ArrowLeft' || event.key === 'a') { keys.left = false; }
    if (event.key === 'ArrowRight' || event.key === 'd') { keys.right = false; }
  });
  canvas.addEventListener('touchstart', function () { confirmKey(); }, { passive: true });
  canvas.addEventListener('touchmove', function (event) {
    var rect = canvas.getBoundingClientRect();
    var target = (event.touches[0].clientX - rect.left) * (W / rect.width);
    keys.left = target < player.x - 6;
    keys.right = target > player.x + 6;
    event.preventDefault();
  }, { passive: false });
  canvas.addEventListener('touchend', function () { keys.left = false; keys.right = false; });
  canvas.addEventListener('click', confirmKey);

  requestAnimationFrame(frame);
})();
"""

GAME_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #05070f; color: #e7ecf5;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
  canvas { width: min(100vw, 480px); height: auto; touch-action: none; border-radius: 14px;
    border: 1px solid rgba(255,255,255,.08); box-shadow: 0 24px 80px rgba(0,0,0,.55); }
</style>
</head>
<body data-title="__TITLE__">
<canvas id="game" width="480" height="640" aria-label="__TITLE__"></canvas>
<script type="application/json" id="sprites">__SPRITES__</script>
<script>__JS__</script>
</body>
</html>
"""
