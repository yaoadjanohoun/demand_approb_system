"""Constructeurs visuels pour les champs JSON destinés aux admins fonctionnels
(voir "Manuel d'Administration Fonctionnel" §3.1 et §4.1).

Chaque widget affiche une interface de type formulaire (au lieu de JSON brut)
et sérialise son état dans un <textarea> caché juste avant la soumission du
formulaire — le champ Django (JSONField + validateurs jsonschema) ne change
pas, donc la validation serveur reste inchangée quel que soit le widget utilisé.
"""
import json

from django import forms
from django.utils.safestring import mark_safe

FIELD_TYPE_CHOICES = [
    ("text", "Texte"),
    ("number", "Nombre entier"),
    ("decimal", "Nombre décimal"),
    ("date", "Date"),
    ("boolean", "Case à cocher"),
    ("file", "Fichier (non supporté pour l'instant)"),
]

CRITERION_TYPES = [
    ("min_amount", "Montant minimum", "number"),
    ("max_amount", "Montant maximum", "number"),
    ("department_ids", "Départements (IDs séparés par des virgules)", "text"),
    ("site_id", "Site (ID)", "number"),
    ("country_code", "Code pays (2 lettres)", "text"),
]


class FormSchemaBuilderWidget(forms.Textarea):
    """Constructeur visuel pour RequestType.form_schema : liste de champs
    (nom technique, label, type, obligatoire) au lieu de JSON brut."""

    def render(self, name, value, attrs=None, renderer=None):
        textarea_html = super().render(name, value, attrs, renderer)
        widget_id = (attrs or {}).get("id", f"id_{name}")
        try:
            initial_fields = json.loads(value).get("fields", []) if value else []
        except (TypeError, ValueError):
            initial_fields = []

        return mark_safe(f"""
<div class="fsb-builder" data-textarea-id="{widget_id}" style="max-width: 720px;">
  <table style="width:100%; border-collapse: collapse;" class="fsb-table">
    <thead>
      <tr>
        <th style="text-align:left; padding:4px;">Nom technique</th>
        <th style="text-align:left; padding:4px;">Label affiché</th>
        <th style="text-align:left; padding:4px;">Type</th>
        <th style="text-align:left; padding:4px;">Obligatoire</th>
        <th></th>
      </tr>
    </thead>
    <tbody class="fsb-rows"></tbody>
  </table>
  <button type="button" class="fsb-add" style="margin-top:8px;">+ Ajouter un champ</button>
  <div style="display:none;">{textarea_html}</div>
</div>
<script>
(function() {{
  const TYPE_OPTIONS = {json.dumps(FIELD_TYPE_CHOICES)};
  const container = document.currentScript.previousElementSibling;
  const textarea = document.getElementById("{widget_id}");
  const rowsBody = container.querySelector(".fsb-rows");
  const addBtn = container.querySelector(".fsb-add");

  function makeRow(field) {{
    field = field || {{name: "", label: "", type: "text", required: false}};
    const tr = document.createElement("tr");
    tr.className = "fsb-row";

    const nameTd = document.createElement("td");
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.className = "fsb-name";
    nameInput.placeholder = "ex: montant";
    nameInput.pattern = "^[a-z_]+$";
    nameInput.title = "Minuscules et underscores uniquement (ex: date_debut)";
    nameInput.value = field.name || "";
    nameInput.style.width = "100%";
    nameTd.appendChild(nameInput);

    const labelTd = document.createElement("td");
    const labelInput = document.createElement("input");
    labelInput.type = "text";
    labelInput.className = "fsb-label";
    labelInput.placeholder = "ex: Montant (€)";
    labelInput.value = field.label || "";
    labelInput.style.width = "100%";
    labelTd.appendChild(labelInput);

    const typeTd = document.createElement("td");
    const typeSelect = document.createElement("select");
    typeSelect.className = "fsb-type";
    TYPE_OPTIONS.forEach(function(opt) {{
      const o = document.createElement("option");
      o.value = opt[0];
      o.textContent = opt[1];
      if (opt[0] === field.type) o.selected = true;
      typeSelect.appendChild(o);
    }});
    typeTd.appendChild(typeSelect);

    const reqTd = document.createElement("td");
    reqTd.style.textAlign = "center";
    const reqInput = document.createElement("input");
    reqInput.type = "checkbox";
    reqInput.className = "fsb-required";
    reqInput.checked = !!field.required;
    reqTd.appendChild(reqInput);

    const delTd = document.createElement("td");
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.textContent = "Supprimer";
    delBtn.className = "fsb-delete";
    delBtn.onclick = function() {{ tr.remove(); }};
    delTd.appendChild(delBtn);

    tr.appendChild(nameTd);
    tr.appendChild(labelTd);
    tr.appendChild(typeTd);
    tr.appendChild(reqTd);
    tr.appendChild(delTd);
    return tr;
  }}

  const initial = {json.dumps(initial_fields)};
  initial.forEach(function(f) {{ rowsBody.appendChild(makeRow(f)); }});

  addBtn.addEventListener("click", function() {{
    rowsBody.appendChild(makeRow());
  }});

  function sync() {{
    const fields = Array.from(rowsBody.querySelectorAll(".fsb-row")).map(function(tr) {{
      return {{
        name: tr.querySelector(".fsb-name").value.trim(),
        label: tr.querySelector(".fsb-label").value.trim(),
        type: tr.querySelector(".fsb-type").value,
        required: tr.querySelector(".fsb-required").checked,
      }};
    }}).filter(function(f) {{ return f.name !== ""; }});
    textarea.value = JSON.stringify({{fields: fields}});
  }}

  const form = container.closest("form");
  if (form) {{
    form.addEventListener("submit", sync);
  }}
}})();
</script>
""")


class CriteriaBuilderWidget(forms.Textarea):
    """Constructeur visuel pour ApprovalRule.criteria : liste de conditions
    (type, valeur) au lieu de JSON brut. Vide = règle par défaut (sans condition)."""

    def render(self, name, value, attrs=None, renderer=None):
        textarea_html = super().render(name, value, attrs, renderer)
        widget_id = (attrs or {}).get("id", f"id_{name}")
        try:
            initial = json.loads(value) if value else {}
        except (TypeError, ValueError):
            initial = {}

        return mark_safe(f"""
<div class="cb-builder" data-textarea-id="{widget_id}" style="max-width: 640px;">
  <div class="cb-rows"></div>
  <button type="button" class="cb-add" style="margin-top:8px;">+ Ajouter un critère</button>
  <p style="color:#6b7280; font-size:0.85em;">Aucun critère = règle par défaut, applicable à toutes les demandes de ce type et niveau.</p>
  <div style="display:none;">{textarea_html}</div>
</div>
<script>
(function() {{
  const CRITERION_TYPES = {json.dumps(CRITERION_TYPES)};
  const container = document.currentScript.previousElementSibling;
  const textarea = document.getElementById("{widget_id}");
  const rowsDiv = container.querySelector(".cb-rows");
  const addBtn = container.querySelector(".cb-add");

  function labelFor(key) {{
    const found = CRITERION_TYPES.find(function(c) {{ return c[0] === key; }});
    return found ? found[1] : key;
  }}
  function inputTypeFor(key) {{
    const found = CRITERION_TYPES.find(function(c) {{ return c[0] === key; }});
    return found ? found[2] : "text";
  }}
  function valueToText(key, val) {{
    if (key === "department_ids") return Array.isArray(val) ? val.join(", ") : "";
    return val === undefined || val === null ? "" : String(val);
  }}

  function makeRow(key, val) {{
    const row = document.createElement("div");
    row.className = "cb-row";
    row.style.cssText = "display:flex; gap:8px; align-items:center; margin-bottom:6px;";

    const select = document.createElement("select");
    select.className = "cb-key";
    CRITERION_TYPES.forEach(function(c) {{
      const o = document.createElement("option");
      o.value = c[0];
      o.textContent = c[1];
      if (c[0] === key) o.selected = true;
      select.appendChild(o);
    }});

    const input = document.createElement("input");
    input.type = "text";
    input.className = "cb-value";
    input.style.flex = "1";
    input.placeholder = key === "department_ids" ? "ex: 10, 12, 15" : "";
    input.value = valueToText(key, val);

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.textContent = "Supprimer";
    delBtn.onclick = function() {{ row.remove(); }};

    row.appendChild(select);
    row.appendChild(input);
    row.appendChild(delBtn);
    return row;
  }}

  const initial = {json.dumps(initial)};
  Object.keys(initial).forEach(function(key) {{
    rowsDiv.appendChild(makeRow(key, initial[key]));
  }});

  addBtn.addEventListener("click", function() {{
    const used = Array.from(rowsDiv.querySelectorAll(".cb-key")).map(function(s) {{ return s.value; }});
    const free = CRITERION_TYPES.find(function(c) {{ return used.indexOf(c[0]) === -1; }});
    rowsDiv.appendChild(makeRow(free ? free[0] : CRITERION_TYPES[0][0]));
  }});

  function sync() {{
    const criteria = {{}};
    Array.from(rowsDiv.querySelectorAll(".cb-row")).forEach(function(row) {{
      const key = row.querySelector(".cb-key").value;
      const raw = row.querySelector(".cb-value").value.trim();
      if (raw === "") return;
      if (key === "department_ids") {{
        criteria[key] = raw.split(",").map(function(s) {{ return parseInt(s.trim(), 10); }}).filter(function(n) {{ return !isNaN(n); }});
      }} else if (key === "min_amount" || key === "max_amount" || key === "site_id") {{
        const n = Number(raw);
        if (!isNaN(n)) criteria[key] = n;
      }} else {{
        criteria[key] = raw;
      }}
    }});
    textarea.value = JSON.stringify(criteria);
  }}

  const form = container.closest("form");
  if (form) {{
    form.addEventListener("submit", sync);
  }}
}})();
</script>
""")
