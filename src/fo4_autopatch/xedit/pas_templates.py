"""Versioned xEdit Pascal bridge scripts with injection-safe rendering.

The templates deliberately implement only two write operations:

* copy an explicitly selected winning record into a *new* override patch; and
* build a *new* override patch containing winning records from selected files.

Neither operation changes a source plugin, renumbers a FormID, compacts a file,
or claims to perform a semantic merge.  Full plugin-eliminating merges remain a
Python-side blocked/experimental workflow.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..config import validate_plugin_name

PAS_BRIDGE_SCHEMA_VERSION = 1
_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_FORM_ID_RE = re.compile(r"^[0-9A-Fa-f]{8}$")


class PascalTemplateError(ValueError):
    """Raised when untrusted data cannot be safely embedded in Pascal."""


def _pascal_string(value: str | Path) -> str:
    text = str(value)
    if not text or len(text) > 32_767:
        raise PascalTemplateError("Pascal string values must contain 1 to 32767 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise PascalTemplateError("Pascal string values cannot contain control characters")
    return "'" + text.replace("'", "''") + "'"


def _plugin_initializers(value: Iterable[str], variable: str) -> str:
    if isinstance(value, (str, bytes)):
        raise PascalTemplateError(f"{variable} must be an iterable of plugin names")
    names: list[str] = []
    seen: set[str] = set()
    for raw in value:
        name = validate_plugin_name(str(raw))
        folded = name.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        names.append(name)
    if len(names) > 4096:
        raise PascalTemplateError("At most 4096 plugin names may be embedded in one script")
    return "\n  ".join(f"{variable}.Add({_pascal_string(name)});" for name in names)


def _operation_initializers(value: Iterable[Mapping[str, Any]]) -> str:
    if isinstance(value, (str, bytes, Mapping)):
        raise PascalTemplateError("operations must be an iterable of mappings")
    result: list[str] = []
    seen: set[tuple[str, str]] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise PascalTemplateError("Every patch operation must be a mapping")
        unknown = set(raw) - {"action", "form_id", "source_plugin"}
        if unknown:
            raise PascalTemplateError(f"Unknown patch operation keys: {sorted(unknown)!r}")
        if raw.get("action", "forward_record") != "forward_record":
            raise PascalTemplateError("Only whole-record forward_record operations are supported")
        form_id = str(raw.get("form_id", "")).upper().removeprefix("0X")
        if not _FORM_ID_RE.fullmatch(form_id):
            raise PascalTemplateError(f"Invalid load-order FormID: {form_id!r}")
        source = validate_plugin_name(str(raw.get("source_plugin", "")))
        key = (form_id, source.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(f"AddOperation({_pascal_string(form_id)}, {_pascal_string(source)});")
    if not result:
        raise PascalTemplateError("At least one forward_record operation is required")
    if len(result) > 100_000:
        raise PascalTemplateError("Too many patch operations for one xEdit session")
    return "\n  ".join(result)


def _render_value(name: str, value: Any) -> str:
    if name in {"selected_plugins", "sources"}:
        return _plugin_initializers(value, "SelectedPlugins")
    if name == "operations":
        return _operation_initializers(value)
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (str, Path)):
        return _pascal_string(value)
    raise PascalTemplateError(f"Unsupported value for Pascal placeholder {name!r}")


def render(template: str, **kw: Any) -> str:
    """Render named placeholders after converting values to Pascal literals.

    This is intentionally not ``str.format``: JSON and Pascal commonly contain
    braces.  Only tokens shaped exactly like ``{identifier}`` are replaced.
    Strings are always quoted and apostrophes are doubled, while structured
    operation/source placeholders go through dedicated validators.
    """

    if not isinstance(template, str) or not template.strip():
        raise PascalTemplateError("A non-empty Pascal template is required")
    required = set(_PLACEHOLDER_RE.findall(template))
    missing = required - set(kw)
    extra = set(kw) - required
    if missing:
        raise PascalTemplateError(f"Missing Pascal template values: {sorted(missing)!r}")
    if extra:
        raise PascalTemplateError(f"Unexpected Pascal template values: {sorted(extra)!r}")
    rendered = _PLACEHOLDER_RE.sub(lambda match: _render_value(match.group(1), kw[match.group(1)]), template)
    if "\x00" in rendered:
        raise PascalTemplateError("Rendered Pascal contains a NUL byte")
    return rendered


CONFLICT_EXPORT_PAS = r'''
unit Fo4AutoPatchConflictExport;

const
  BridgeSchemaVersion = 1;

var
  OutputPath: string;
  Lines: TStringList;
  SelectedPlugins: TStringList;
  FirstRecord: Boolean;

function JsonEscape(const Value: string): string;
begin
  Result := StringReplace(Value, '\', '\\', [rfReplaceAll]);
  Result := StringReplace(Result, '"', '\"', [rfReplaceAll]);
  Result := StringReplace(Result, #13, '\r', [rfReplaceAll]);
  Result := StringReplace(Result, #10, '\n', [rfReplaceAll]);
  Result := StringReplace(Result, #9, '\t', [rfReplaceAll]);
end;

function JsonString(const Value: string): string;
begin
  Result := '"' + JsonEscape(Value) + '"';
end;

function JsonBool(const Value: Boolean): string;
begin
  if Value then
    Result := 'true'
  else
    Result := 'false';
end;

function PluginSelected(const BaseRecord: IInterface): Boolean;
var
  I: Integer;
  Candidate: IInterface;
begin
  Result := SelectedPlugins.Count = 0;
  if Result then
    Exit;
  if SelectedPlugins.IndexOf(GetFileName(BaseRecord)) >= 0 then begin
    Result := True;
    Exit;
  end;
  for I := 0 to OverrideCount(BaseRecord) - 1 do begin
    Candidate := OverrideByIndex(BaseRecord, I);
    if SelectedPlugins.IndexOf(GetFileName(Candidate)) >= 0 then begin
      Result := True;
      Exit;
    end;
  end;
end;

function OverrideChainJson(const BaseRecord: IInterface): string;
var
  I: Integer;
begin
  Result := '[' + JsonString(GetFileName(BaseRecord));
  for I := 0 to OverrideCount(BaseRecord) - 1 do
    Result := Result + ',' + JsonString(GetFileName(OverrideByIndex(BaseRecord, I)));
  Result := Result + ']';
end;

function LosingPluginsJson(const BaseRecord, Winner: IInterface): string;
var
  I: Integer;
  Candidate: IInterface;
  First: Boolean;
begin
  Result := '[';
  First := True;
  if not Equals(BaseRecord, Winner) then begin
    Result := Result + JsonString(GetFileName(BaseRecord));
    First := False;
  end;
  for I := 0 to OverrideCount(BaseRecord) - 1 do begin
    Candidate := OverrideByIndex(BaseRecord, I);
    if not Equals(Candidate, Winner) then begin
      if not First then
        Result := Result + ',';
      Result := Result + JsonString(GetFileName(Candidate));
      First := False;
    end;
  end;
  Result := Result + ']';
end;

function Initialize: Integer;
var
  I: Integer;
begin
  Result := 0;
  OutputPath := {out_json};
  Lines := TStringList.Create;
  SelectedPlugins := TStringList.Create;
  SelectedPlugins.CaseSensitive := False;
  {selected_plugins}
  Lines.Add('{"schema_version":' + IntToStr(BridgeSchemaVersion) +
    ',"kind":"xedit_conflict_scan","scanned_plugins":[');
  for I := 0 to FileCount - 1 do begin
    if I > 0 then
      Lines[Lines.Count - 1] := Lines[Lines.Count - 1] + ',';
    Lines[Lines.Count - 1] := Lines[Lines.Count - 1] + JsonString(GetFileName(FileByIndex(I)));
  end;
  Lines[Lines.Count - 1] := Lines[Lines.Count - 1] + '],"records":[';
  FirstRecord := True;
end;

function Process(E: IInterface): Integer;
var
  BaseRecord, Winner: IInterface;
  CheckError, Sig, Payload: string;
  HasVMAD, HasPrecombine, IsDeletedRecord: Boolean;
begin
  Result := 0;
  if not IsWinningOverride(E) then
    Exit;
  BaseRecord := MasterOrSelf(E);
  CheckError := Check(E);
  if (OverrideCount(BaseRecord) = 0) and (CheckError = '') then
    Exit;
  if not PluginSelected(BaseRecord) then
    Exit;
  Winner := WinningOverride(BaseRecord);
  Sig := Signature(E);
  HasVMAD := ElementExists(E, 'VMAD');
  IsDeletedRecord := GetIsDeleted(E);
  HasPrecombine := False;
  if Sig = 'CELL' then
    HasPrecombine := HasPrecombinedMesh(E);
  Payload := '{"form_id":' + JsonString(IntToHex(GetLoadOrderFormID(E), 8)) +
    ',"signature":' + JsonString(Sig) +
    ',"edid":' + JsonString(EditorID(E)) +
    ',"winning_plugin":' + JsonString(GetFileName(Winner)) +
    ',"losing_plugins":' + LosingPluginsJson(BaseRecord, Winner) +
    ',"override_chain":' + OverrideChainJson(BaseRecord) +
    ',"fields":[],"field_sources":{},"metadata":{' +
    '"evidence_complete":false,"source_values_complete":false,' +
    '"is_deleted":' + JsonBool(IsDeletedRecord) +
    ',"has_vmad":' + JsonBool(HasVMAD) +
    ',"has_precombine":' + JsonBool(HasPrecombine) +
    ',"check_error":' + JsonString(CheckError) + '}}';
  if not FirstRecord then
    Lines[Lines.Count - 1] := Lines[Lines.Count - 1] + ',';
  Lines[Lines.Count - 1] := Lines[Lines.Count - 1] + Payload;
  FirstRecord := False;
end;

function Finalize: Integer;
begin
  Result := 0;
  Lines[Lines.Count - 1] := Lines[Lines.Count - 1] + '],"ok":true}';
  Lines.SaveToFile(OutputPath);
  SelectedPlugins.Free;
  Lines.Free;
end;

end.
'''.strip()


BATCH_EDIT_PAS = r'''
unit Fo4AutoPatchBatchOverride;

const
  BridgeSchemaVersion = 1;

var
  ResultPath, PatchName: string;
  Operations, Errors: TStringList;
  PatchFile: IInterface;
  CopiedCount: Integer;

function JsonEscape(const Value: string): string;
begin
  Result := StringReplace(Value, '\', '\\', [rfReplaceAll]);
  Result := StringReplace(Result, '"', '\"', [rfReplaceAll]);
  Result := StringReplace(Result, #13, '\r', [rfReplaceAll]);
  Result := StringReplace(Result, #10, '\n', [rfReplaceAll]);
end;

function JsonString(const Value: string): string;
begin
  Result := '"' + JsonEscape(Value) + '"';
end;

procedure AddOperation(const FormIDText, SourcePlugin: string);
begin
  Operations.Add(FormIDText + '|' + SourcePlugin);
end;

procedure SaveResult(const IsOK: Boolean; const ErrorText: string);
var
  OutLines: TStringList;
  Payload: string;
begin
  OutLines := TStringList.Create;
  if IsOK then
    Payload := '{"schema_version":' + IntToStr(BridgeSchemaVersion) +
      ',"kind":"xedit_override_patch","ok":true,"created_plugin":' +
      JsonString(PatchName) + ',"copied_records":' + IntToStr(CopiedCount) + '}'
  else
    Payload := '{"schema_version":' + IntToStr(BridgeSchemaVersion) +
      ',"kind":"xedit_override_patch","ok":false,"error":' +
      JsonString(ErrorText) + '}';
  OutLines.Add(Payload);
  OutLines.SaveToFile(ResultPath);
  OutLines.Free;
end;

function Initialize: Integer;
var
  I, DelimiterPos: Integer;
  Entry, FormIDText, SourceName: string;
  SourceFile, SourceRecord: IInterface;
begin
  Result := 0;
  ResultPath := {out_json};
  PatchName := {patch_name};
  CopiedCount := 0;
  Operations := TStringList.Create;
  Errors := TStringList.Create;
  {operations}

  if Assigned(wbFileByName(PatchName)) then begin
    SaveResult(False, 'Refusing to modify an existing patch: ' + PatchName);
    Result := 1;
    Exit;
  end;

  for I := 0 to Operations.Count - 1 do begin
    Entry := Operations[I];
    DelimiterPos := Pos('|', Entry);
    FormIDText := Copy(Entry, 1, DelimiterPos - 1);
    SourceName := Copy(Entry, DelimiterPos + 1, Length(Entry));
    SourceFile := wbFileByName(SourceName);
    if not Assigned(SourceFile) then
      Errors.Add('Source plugin is not loaded: ' + SourceName)
    else begin
      SourceRecord := RecordByFormID(
        SourceFile,
        LoadOrderFormIDtoFileFormID(SourceFile, StrToInt('$' + FormIDText)),
        True
      );
      if not Assigned(SourceRecord) then
        Errors.Add('Record ' + FormIDText + ' was not found in ' + SourceName);
    end;
  end;

  if Errors.Count > 0 then begin
    SaveResult(False, Errors.CommaText);
    Result := 1;
    Exit;
  end;

  PatchFile := AddNewFileName(PatchName);
  if not Assigned(PatchFile) then begin
    SaveResult(False, 'xEdit could not create ' + PatchName);
    Result := 1;
    Exit;
  end;

  for I := 0 to Operations.Count - 1 do begin
    Entry := Operations[I];
    DelimiterPos := Pos('|', Entry);
    FormIDText := Copy(Entry, 1, DelimiterPos - 1);
    SourceName := Copy(Entry, DelimiterPos + 1, Length(Entry));
    SourceFile := wbFileByName(SourceName);
    SourceRecord := RecordByFormID(
      SourceFile,
      LoadOrderFormIDtoFileFormID(SourceFile, StrToInt('$' + FormIDText)),
      True
    );
    AddRequiredElementMasters(SourceRecord, PatchFile, False);
    wbCopyElementToFile(SourceRecord, PatchFile, False, True);
    Inc(CopiedCount);
  end;
  CleanMasters(PatchFile);
  SortMasters(PatchFile);
end;

function Finalize: Integer;
begin
  Result := 0;
  if Assigned(PatchFile) then
    SaveResult(True, '');
  Errors.Free;
  Operations.Free;
end;

end.
'''.strip()


MERGE_PAS = r'''
unit Fo4AutoPatchOverrideMerge;

const
  BridgeSchemaVersion = 1;

var
  ResultPath, PatchName: string;
  SelectedPlugins: TStringList;
  PatchFile: IInterface;
  CopiedCount: Integer;
  DryRun: Boolean;

function JsonEscape(const Value: string): string;
begin
  Result := StringReplace(Value, '\', '\\', [rfReplaceAll]);
  Result := StringReplace(Result, '"', '\"', [rfReplaceAll]);
  Result := StringReplace(Result, #13, '\r', [rfReplaceAll]);
  Result := StringReplace(Result, #10, '\n', [rfReplaceAll]);
end;

function JsonString(const Value: string): string;
begin
  Result := '"' + JsonEscape(Value) + '"';
end;

procedure SaveResult(const IsOK: Boolean; const ErrorText: string);
var
  OutLines: TStringList;
  Payload: string;
begin
  OutLines := TStringList.Create;
  if IsOK then
    Payload := '{"schema_version":' + IntToStr(BridgeSchemaVersion) +
      ',"kind":"xedit_override_merge","ok":true,"created_plugin":' +
      JsonString(PatchName) + ',"copied_records":' + IntToStr(CopiedCount) +
      ',"sources_retained":true}'
  else
    Payload := '{"schema_version":' + IntToStr(BridgeSchemaVersion) +
      ',"kind":"xedit_override_merge","ok":false,"error":' +
      JsonString(ErrorText) + '}';
  OutLines.Add(Payload);
  OutLines.SaveToFile(ResultPath);
  OutLines.Free;
end;

function Initialize: Integer;
var
  I: Integer;
begin
  Result := 0;
  ResultPath := {out_json};
  PatchName := {output_name};
  DryRun := {dry_run};
  CopiedCount := 0;
  SelectedPlugins := TStringList.Create;
  SelectedPlugins.CaseSensitive := False;
  {sources}
  if DryRun then begin
    SaveResult(True, '');
    Exit;
  end;
  if Assigned(wbFileByName(PatchName)) then begin
    SaveResult(False, 'Refusing to modify an existing output plugin: ' + PatchName);
    Result := 1;
    Exit;
  end;
  for I := 0 to SelectedPlugins.Count - 1 do
    if not Assigned(wbFileByName(SelectedPlugins[I])) then begin
      SaveResult(False, 'Source plugin is not loaded: ' + SelectedPlugins[I]);
      Result := 1;
      Exit;
    end;
  PatchFile := AddNewFileName(PatchName);
  if not Assigned(PatchFile) then begin
    SaveResult(False, 'xEdit could not create ' + PatchName);
    Result := 1;
  end;
end;

function Process(E: IInterface): Integer;
begin
  Result := 0;
  if DryRun or not Assigned(PatchFile) then
    Exit;
  if SelectedPlugins.IndexOf(GetFileName(E)) < 0 then
    Exit;
  if not IsWinningOverride(E) then
    Exit;
  AddRequiredElementMasters(E, PatchFile, False);
  wbCopyElementToFile(E, PatchFile, False, True);
  Inc(CopiedCount);
end;

function Finalize: Integer;
begin
  Result := 0;
  if Assigned(PatchFile) then begin
    CleanMasters(PatchFile);
    SortMasters(PatchFile);
    SaveResult(True, '');
  end;
  SelectedPlugins.Free;
end;

end.
'''.strip()


def render_conflict_export(out_json: str | Path, plugins: Iterable[str] | None = None) -> str:
    """Render the read-only conflict/error exporter."""

    return render(CONFLICT_EXPORT_PAS, out_json=Path(out_json), selected_plugins=plugins or ())


def render_batch_patch(
    out_json: str | Path,
    patch_name: str,
    operations: Iterable[Mapping[str, Any]],
) -> str:
    """Render whole-record forwarding into a new override patch."""

    validate_plugin_name(patch_name)
    if Path(patch_name).suffix.casefold() != ".esp":
        raise PascalTemplateError("Generated conflict patches must use the .esp extension")
    return render(BATCH_EDIT_PAS, out_json=Path(out_json), patch_name=patch_name, operations=operations)


def render_override_merge(
    out_json: str | Path,
    output_name: str,
    sources: Iterable[str],
    *,
    dry_run: bool = False,
) -> str:
    """Render a source-retaining override patch, never a full merge."""

    validate_plugin_name(output_name)
    if Path(output_name).suffix.casefold() != ".esp":
        raise PascalTemplateError("Override merge output must use the .esp extension")
    source_list = list(sources)
    if not source_list:
        raise PascalTemplateError("At least one source plugin is required")
    return render(
        MERGE_PAS,
        out_json=Path(out_json),
        output_name=output_name,
        sources=source_list,
        dry_run=dry_run,
    )
