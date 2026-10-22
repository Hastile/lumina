// Every half second, the video this frame is playing (players often live in
// an iframe, so this runs in every frame). The background script adds the
// tab's title and address and hands it to the local recorder.
setInterval(() => {
  const videos = [...document.querySelectorAll("video")].filter((v) => v.duration > 0);
  if (!videos.length) return;
  const area = (v) => v.clientWidth * v.clientHeight;
  const v = videos.sort((a, b) => (a.paused - b.paused) || (area(b) - area(a)))[0];
  try {
    chrome.runtime.sendMessage({ currentTime: v.currentTime, duration: v.duration, paused: v.paused });
  } catch (e) {
    // the extension was reloaded; this page keeps the old script until it reloads
  }
}, 500);
