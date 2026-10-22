// Every half second, the video this frame is playing (players often live in
// an iframe, so this runs in every frame). The background script adds the
// tab's title and address, hands it to the local recorder, and passes back
// what the recorder wants done: seek, play, pause, or open another page.
function pick() {
  const videos = [...document.querySelectorAll("video")].filter((v) => v.duration > 0);
  if (!videos.length) return null;
  const area = (v) => v.clientWidth * v.clientHeight;
  return videos.sort((a, b) => (a.paused - b.paused) || (area(b) - area(a)))[0];
}

function apply(cmd, v) {
  if (!cmd) return;
  if (cmd.navigate) {
    window.top.location.href = cmd.navigate;
    return;
  }
  if (typeof cmd.seek === "number") v.currentTime = cmd.seek;
  if (cmd.play) v.play().catch(() => {});
  if (cmd.pause) v.pause();
}

setInterval(() => {
  const v = pick();
  if (!v) return;
  try {
    chrome.runtime.sendMessage(
      { currentTime: v.currentTime, duration: v.duration, paused: v.paused, seeking: v.seeking },
      (cmd) => apply(cmd, v),
    );
  } catch (e) {
    // the extension was reloaded; this page keeps the old script until it reloads
  }
}, 500);
