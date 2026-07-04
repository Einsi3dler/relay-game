// Fallback renderer (T5.2) — renders any text or multiple-choice puzzle from
// prompt + payload per GAME_MODULE_SPEC §6. Used when no game-specific
// renderer is registered for a game_id.
(function () {
  "use strict";

  var state = null;

  window.RelayGames = window.RelayGames || {};
  window.RelayGames.fallback = {
    mount: function (container, puzzle, api) {
      state = { container: container };
      var payload = puzzle.payload || {};
      if (payload.hint) {
        var hint = document.createElement("p");
        hint.className = "muted";
        hint.textContent = "Hint: " + payload.hint;
        container.appendChild(hint);
      }
      if (Array.isArray(payload.options)) {
        payload.options.forEach(function (option) {
          var button = document.createElement("button");
          button.type = "button";
          button.className = "submit";
          button.style.marginRight = "8px";
          button.textContent = option;
          button.addEventListener("click", function () { api.submit(String(option)); });
          container.appendChild(button);
        });
      } else {
        var input = document.createElement("input");
        input.placeholder = "Your answer";
        input.autocomplete = "off";
        var submit = document.createElement("button");
        submit.type = "button";
        submit.className = "submit";
        submit.textContent = "Submit";
        var send = function () { if (input.value.trim()) api.submit(input.value); };
        submit.addEventListener("click", send);
        input.addEventListener("keydown", function (event) {
          if (event.key === "Enter") send();
        });
        container.appendChild(input);
        container.appendChild(submit);
        input.focus();
      }
    },
    unmount: function () {
      if (state && state.container) state.container.innerHTML = "";
      state = null;
    },
  };
})();
