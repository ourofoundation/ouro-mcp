# Hosted file uploads: signed upload URLs

Status: built 2026-10-02 in backend, ouro-py 2.3.0 and ouro-mcp 0.11.0. This
note began as the proposal; "What was built" records where the code differs.

## What was built

- `POST /files/upload-url` and `POST /files/upload/complete` in the backend,
  `files.create_upload_url()` and `upload_id=` on `files.create()` /
  `files.update()` in ouro-py, and the `create_upload_url` tool plus an
  `upload_id` parameter on `create_file` / `update_file` in the MCP server.
- `upload_id` is plain `<bucket>/<path>`, not an HMAC-signed token. The
  complete endpoint only accepts a path under the caller's own user id in one
  of the two file buckets, which is the same ownership rule
  `adoptReplacementUpload` already applies.
- The MCP tool takes only `file_name`. Uploads are reserved in the private
  bucket and `/files/create` moves the object if the asset turns out public,
  so the agent does not have to decide visibility twice.
- `update` goes through `PUT /files/:id` with the uploaded object's bucket and
  path in `metadata`; the backend copies the bytes onto the asset's own path.
- A plain `PUT` with a raw body works against Supabase's signed upload URL,
  so the returned command is a single `curl --data-binary`.
- Posts and datasets take `upload_id` too. The MCP server reads the upload
  back (`files.read_upload`, through `POST /files/upload/complete` with
  `download: true`), uses the existing markdown and row-ingest paths, and
  discards the upload (`DELETE /files/upload`) once the asset exists.
- Services take `spec_upload_id`. The backend reads the upload, stores its own
  copy in `service-specs` as it does for `spec_url`, and removes the upload.
- Users cannot delete storage objects, and a refused delete returns no error.
  Uploads are removed with the service role after `parseUploadId` has checked
  ownership. This also fixed replacement uploads on file update, which had
  been leaving an orphan behind every time.
- `PUT /services/:id/update/from-file` now refuses a `spec_path` outside the
  service owner's or caller's folder. It reads that path with the service
  role and had no ownership check.
- Downloads: `POST /assets/:id/download-url`, used by `download_asset` when it
  has no `output_path` (always, on the hosted server) to return a link and a
  `curl` command instead of writing a file. One tool, not two.
  A file gets its storage URL. A dataset (CSV) or post (markdown or HTML) gets
  a link to `GET /assets/:id/download?token=...`, signed with a key derived
  from `SUPABASE_JWT_SECRET` and valid for 15 minutes. Access is checked as
  the caller when the link is issued; the link is served with the service
  role. Post markdown comes from `lib/editor/json-to-markdown.ts`, a port of
  ouro-py's `tiptap_to_markdown`: keep the two in step.
- Not built: a sweep for uploads that are never used, and `source_url`.

## Problem

The hosted MCP server cannot read the caller's files, so the only way to upload
one is inline: `file_content_text` or `file_content_base64`. Inline base64 has
to be written out by the model token by token. A 35 KB PNG is about 46,000
characters, which is slow, expensive, and one wrong character corrupts the file.
In practice binary uploads through the hosted server do not happen; the pigment
palette post went up as hand-written SVG for this reason.

The agent almost always has a shell. It can move bytes itself if it is handed
somewhere to put them.

## Flow

```
agent                      MCP (hosted)                 backend                 storage
  | create_upload_url ------->|                            |                       |
  |                           | POST /files/upload-url --->|                       |
  |                           |                            | createSignedUploadUrl |
  |<-- upload_url, upload_id -|<---------------------------|                       |
  | curl -X PUT --data-binary @file <upload_url> ------------------------------->  |
  | create_file(upload_id) -->|                            |                       |
  |                           | POST /files/create ------->| verify object, create |
  |<-- file asset ------------|<---------------------------|                       |
```

## Backend

Today `POST /files/upload` takes `{file_name, file_base64, visibility, content_type}`,
writes to `{bucket for visibility}/{user.id}/{uuidv7}{ext}` and returns
`{id, bucket, path, size, mime_type, file_name}`. `POST /files/create` then
makes the asset from that object. The new endpoint replaces only the first step.

### `POST /files/upload-url`

Request: `{file_name, visibility = "private", content_type?}`

1. `requireRouteUser`, same as `uploadFileBytes`.
2. Pick the bucket and path exactly as `uploadFileBytes` does:
   `getBucketForVisibility(visibility)`, `${user.id}/${uuidv7()}${ext}`.
3. `supabase.storage.from(bucket).createSignedUploadUrl(path)`.
4. Return:

```json
{
  "upload_id": "<opaque>",
  "upload_url": "https://.../object/upload/sign/<bucket>/<path>?token=...",
  "method": "PUT",
  "headers": {"content-type": "image/png"},
  "expires_at": "2026-10-02T16:15:00Z",
  "max_bytes": 104857600
}
```

`upload_id` carries `{bucket, path, file_name, content_type, user_id, exp}`,
signed with a server secret (HMAC). The client never chooses the bucket or
path again, so it cannot point `create_file` at another user's object.

### `POST /files/upload/complete` (or fold into `/files/create`)

Request: `{upload_id}`

1. Verify the signature and expiry, and that `user_id` matches the caller.
2. Look the object up in `storage.objects` by bucket and path, as
   `uploadFileBytes` already does after its own upload. 404 if nothing was uploaded.
3. Infer the MIME type server-side from the stored bytes and file name
   (`inferFileMimeType`); do not trust the upload's header.
4. Return the same shape `/files/upload` returns, so `/files/create`,
   `PUT /files/:id` and the SDK need no other change.

## SDK (ouro-py)

- `files.create_upload_url(file_name, visibility, content_type=None)`
- `files.create(..., upload_id=...)` and `files.update(..., upload_id=...)`
  call `/files/upload/complete` in place of `_upload_content`.
- `_upload_local_file` can switch to the same flow for large files, which also
  lifts the 100 MB JSON body limit on `/files/upload`.

## MCP

- New tool `create_upload_url(file_name, visibility?)`. The result includes the
  exact command to run, so the agent does not assemble it:

  ```
  curl -sS -X PUT -H 'content-type: image/png' --data-binary @<path> '<upload_url>'
  ```

- `create_file` and `update_file` gain `upload_id`, alongside the inline
  content parameters. Offered on both transports: on stdio it is simply
  another option beside `file_path`.
- Phase 2: `create_dataset(upload_id=...)` and `update_dataset(upload_id=...)`
  for large CSV or Parquet, which needs the backend to ingest from storage
  instead of from request rows.

## Downloads

`download_asset` is removed from the hosted server for the same reason. The
counterpart already exists: `GET /files/:id/data` returns a signed download URL
(`files.read_data` in the SDK). A hosted `get_download_url(id)` tool returning
that URL and a `curl -o` command would close the loop. Datasets need a CSV
export URL.

## Security

- The upload URL is a bearer credential. Keep the expiry short (Supabase's
  default is two hours; minutes would do) and never log it.
- The path is namespaced by user id, as today. `upload_id` is signed, so bucket
  and path are fixed at issue time.
- Size is capped by the bucket's file size limit. Report `max_bytes` so the
  agent can fail early.
- An upload that is never completed leaves an orphan object. `/files/upload`
  has the same gap when `create` is never called; one sweep job that deletes
  objects with no asset after a day covers both.
- Visibility picks the bucket at issue time. If the asset is created with a
  different visibility, move the object or reject; decide which.

## Alternative: `source_url`

`create_file(source_url=...)` has the server download the file through the
existing safe-fetch SSRF guard. Much less to build, but it only helps when the
file is already on the public web, which is not the agent-made-a-plot case.
Worth adding as a second option, not as the fix.

## Open questions

- Do the agent sandboxes we care about (Claude Code, claude.ai connectors,
  our own agents) allow outbound `curl` to the storage host? If a sandbox
  blocks it, that client still needs inline content.
- Does the signed upload URL accept a plain `PUT` with a raw body from `curl`,
  or only the multipart form `uploadToSignedUrl` sends? Check against the
  Supabase version we run before fixing the documented command.
- One-time use: can a signed upload URL be reused until it expires, and does
  that matter given `upsert: false`?
