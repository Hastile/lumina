// Forward a frame's playback report, with its tab's title and address, to the
// Lumina recorder on this computer, and hand its answer (a command for this
// frame, if any) back to the frame. When the recorder is not running the
// request just fails.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!sender.tab) return;
  fetch("http://127.0.0.1:47321/playback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tab: `${sender.tab.id}:${sender.frameId}`,
      title: sender.tab.title,
      url: sender.tab.url,
      currentTime: msg.currentTime,
      duration: msg.duration,
      paused: msg.paused,
      seeking: msg.seeking,
    }),
  })
    .then((r) => (r.status === 200 ? r.json() : null))
    .then((cmd) => sendResponse(cmd))
    .catch(() => sendResponse(null));
  return true; // the answer comes asynchronously
});
