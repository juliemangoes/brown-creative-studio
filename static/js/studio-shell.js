/* Brown Creative Studio shell: the theme choice and the Cmd/Ctrl+K command menu.
   Loaded as a static file so the content security policy needs no inline code. */
(function () {
  var KEY = 'studio-theme';
  window.studioTheme = {
    get: function () {
      try { return localStorage.getItem(KEY) || 'system'; } catch (e) { return 'system'; }
    },
    set: function (theme) {
      try {
        if (theme === 'light' || theme === 'dark') { localStorage.setItem(KEY, theme); } else { localStorage.removeItem(KEY); }
      } catch (e) { /* private mode: the choice lasts for this page only */ }
      if (theme === 'light' || theme === 'dark') {
        document.documentElement.setAttribute('data-theme', theme);
      } else {
        document.documentElement.removeAttribute('data-theme');
      }
      document.dispatchEvent(new CustomEvent('studio:themechange', { detail: { theme: theme } }));
    }
  };
})();

function studioCommandMenu() {
  return {
    open: false,
    query: '',
    active: 0,
    commands: [],
    returnFocus: null,
    init: function () {
      var el = document.getElementById('studio-commands');
      try { this.commands = el ? JSON.parse(el.textContent) : []; } catch (e) { this.commands = []; }
    },
    get results() {
      var q = this.query.trim().toLowerCase();
      if (!q) { return this.commands; }
      return this.commands.filter(function (c) { return (c.label + ' ' + c.group).toLowerCase().indexOf(q) !== -1; });
    },
    show: function () {
      this.returnFocus = document.activeElement;
      this.query = '';
      this.active = 0;
      this.open = true;
      var self = this;
      this.$nextTick(function () { self.$refs.input.focus(); });
    },
    hide: function () {
      this.open = false;
      if (this.returnFocus && this.returnFocus.focus) { this.returnFocus.focus(); }
    },
    onGlobalKey: function (e) {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        if (this.open) { this.hide(); } else { this.show(); }
      }
    },
    move: function (delta) {
      var n = this.results.length;
      if (!n) { return; }
      this.active = (this.active + delta + n) % n;
      var id = 'studio-command-' + this.active;
      this.$nextTick(function () {
        var option = document.getElementById(id);
        if (option) { option.scrollIntoView({ block: 'nearest' }); }
      });
    },
    run: function (cmd) {
      if (!cmd) { return; }
      this.hide();
      if (cmd.theme) { window.studioTheme.set(cmd.theme); return; }
      if (cmd.event) { window.dispatchEvent(new CustomEvent(cmd.event)); return; }
      if (cmd.url) { window.location.href = cmd.url; }
    }
  };
}
