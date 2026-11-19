// Task board: drag a card to another column to change its status.
// Each card also has a Move menu, so nothing depends on dragging (WCAG 2.5.7).
(function () {
  function csrf() {
    var input = document.querySelector('[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }
  function init() {
    if (typeof Sortable === 'undefined') return;
    document.querySelectorAll('[data-board-list]').forEach(function (list) {
      Sortable.create(list, {
        group: 'tasks',
        draggable: '[data-movable]',
        animation: 150,
        onAdd: function (evt) {
          var card = evt.item;
          var status = evt.to.getAttribute('data-status');
          var body = new URLSearchParams({ status: status, position: String(evt.newIndex) });
          fetch(card.getAttribute('data-task-url'), {
            method: 'POST',
            headers: { 'X-CSRFToken': csrf(), Accept: 'application/json' },
            body: body,
            credentials: 'same-origin',
          }).then(function (response) {
            if (!response.ok) throw new Error('status ' + response.status);
            var select = card.querySelector('select[name=status]');
            if (select) select.value = status;
            document.querySelectorAll('[data-board-list]').forEach(function (col) {
              var count = col.parentElement.querySelector('.board-column-title span:last-child');
              if (count) count.textContent = col.querySelectorAll('[data-task-url]').length;
              var empty = col.querySelector('[data-empty]');
              if (empty) empty.hidden = col.querySelectorAll('[data-task-url]').length > 0;
            });
          }).catch(function () {
            window.location.reload();
          });
        },
      });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
