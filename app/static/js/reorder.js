(function () {
  const list = document.getElementById("queue-list");
  if (!list || typeof Sortable === "undefined") return;

  const csrfToken = list.dataset.csrf;
  const urlBase = list.dataset.reorderUrlBase;

  Sortable.create(list, {
    handle: ".drag-handle",
    animation: 150,
    onEnd: function (evt) {
      const item = evt.item;
      const requestId = item.dataset.requestId;
      const newPosition = evt.newIndex + 1;

      const body = new URLSearchParams();
      body.set("csrf_token", csrfToken);
      body.set("position", String(newPosition));

      fetch(`${urlBase}/${requestId}/reorder`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      })
        .then((resp) => {
          if (!resp.ok) throw new Error("Reorder failed");
          return resp.json();
        })
        .then(() => window.location.reload())
        .catch(() => window.location.reload());
    },
  });
})();
