# Publisher release publication

Catalogue is an optional index at https://catalogue.prancing-dreadnaught.com.
A Publisher is a Catalogue account, separate from Participant accounts. The
first approved publication atomically binds a random UUIDv4 Package ID to its
Publisher. Review validates the listing's metadata and hosted Manifest; it
does not test, host, freeze, sign or certify Package contents. Manager's normal
archive checks and installation transactions still apply.

## Prepare public hosting

Choose an exact new version and the authoritative public HTTPS `manifest` and
`download` URLs in `rookframe.json` before building. Preserve the Package ID.
Commit and push the clean author project, including sources, scenes, any optional library inventory, presets, documentation and tests. Run the normal
`check` and `build` commands. Keep the one checked complete output; a
retry uploads those same bytes without rebuilding.

Any public HTTPS host works. Upload the exact `rookframe.json` bytes, the one
`.rookpackage` archive, and every declared media file. Each media file must be
public at its explicit `url`, or at its filename beside the archive when `url`
is omitted. Verify all URLs are public without credentials.
Never rewrite a released version's assets or move its tag. Publish a new version
when bytes need to change.

## Optional GitHub release command

Install GitHub CLI from its official distribution and sign in to the intended
public repository. `gh` owns GitHub credentials; the plugin never stores or sends
them to Catalogue. Put URLs with simple filenames in the Manifest:

```json
{
  "manifest": "https://github.com/OWNER/REPO/releases/download/v0.9.0/rookframe.json",
  "download": "https://github.com/OWNER/REPO/releases/download/v0.9.0/Calendar-0.9.0.rookpackage"
}
```

Inspect the destination and checked build first (no writes):

```sh
python3 addons/rookframe_development/rookframe_authoring.py publish-github \
  --project . --archive build/Calendar-0.9.0.rookpackage \
  --repo OWNER/REPO --tag v0.9.0 --commit FULL_40_CHARACTER_SOURCE_COMMIT
```

Repeat with `--confirm` to create a draft, upload and read back the exact
Manifest and archive, then publish. Optional `--notes release-notes.md` supplies
the release description. The tool verifies the production artifact, configured
shared artifact, source identity, clean local commit, remote commit, public repository,
and the version tag. Existing assets are downloaded and compared byte for byte
before retry decisions. Different bytes, ambiguous assets, or a moved tag stop
the operation. It does not overwrite or delete anything.

If a request fails, retain its `externalProgress` JSON. GitHub may already have
created the draft or an asset. Rerun the same command with the same archive and
commit to inspect remote state and finish missing steps. A successful GitHub
release does not imply a Catalogue submission, and a failed Catalogue request
does not roll back or delete GitHub assets.

## Cover images and videos

Use the authoring CLI to attach a cover image or gallery item before building.
Systems use the same media contract; Rookframe can use their cover as the default
image when creating a game. The commands below update the Package Manifest.

```sh
python3 addons/rookframe_development/rookframe_authoring.py media --project . --role cover --file /path/to/cover.png
python3 addons/rookframe_development/rookframe_authoring.py media --project . --role gallery --file /path/to/tour.webm --caption "Package tour"
```

Media attachment selects Manifest format 2, which requires a Rookframe version
with format-2 support. Images accept PNG, JPEG or WebP up to 16 MiB; videos accept
OGV, MP4 or WebM up to 128 MiB. A cover must be an image. Up to 12 gallery items
are supported. Media are packaged as inert files outside executable PCK data.
GitHub publication uploads the checked media beside the archive and Manifest.
Catalogue reads the public HTTPS URLs and displays the images and video controls.
Changing a published release's artwork requires a new Package version.

## Register and authenticate

Use the web portal or the authoring CLI. Password prompts do not echo or accept
passwords in command arguments. Email confirmation is required before sign-in.

```sh
python3 addons/rookframe_development/rookframe_authoring.py catalogue register --email YOU --name "Publisher name"
# Follow the confirmation email in a browser, or enter its token privately:
python3 addons/rookframe_development/rookframe_authoring.py catalogue verify
python3 addons/rookframe_development/rookframe_authoring.py catalogue login --email YOU
```

`catalogue resend --email YOU` sends another verification link. Password reset
is available in the portal, or start it with `catalogue forgot-password --email
YOU`. Reset revokes all active sessions and API tokens.

Login creates a revocable 90-day API token in an owner-readable credential file
under `~/.config/rookframe-authoring/`, keyed by the exact Catalogue origin.
`ROOKFRAME_AUTHOR_CREDENTIALS` can select a private directory, and
`ROOKFRAME_CATALOGUE_TOKEN` can supply a separately created token from the portal.
Keep these outside Package source and archives. Use `catalogue tokens` to list
credentials and `catalogue logout` to revoke this installation's saved token.
An environment-supplied token can be revoked with `logout --token-id ID`.
API tokens cannot perform administrator actions.

## Inspect, submit, and read back

```sh
python3 addons/rookframe_development/rookframe_authoring.py catalogue propose \
  --manifest https://YOUR_PUBLIC_HOST/rookframe.json --output proposal.json
# Read the returned exact ID, version, links and ordinary metadata first.
python3 addons/rookframe_development/rookframe_authoring.py catalogue submit --proposal proposal.json
python3 addons/rookframe_development/rookframe_authoring.py catalogue submit --proposal proposal.json --confirm --note "Ready for review"
python3 addons/rookframe_development/rookframe_authoring.py catalogue status
python3 addons/rookframe_development/rookframe_authoring.py catalogue status --id SUBMISSION_UUID
```

A proposal expires after one hour. Creating it or submitting for review does not
make the release public or reserve the Package ID. Submission refetches the
Manifest and rejects changed bytes. Approval refetches again and publishes the
record and first-owner claim in one PostgreSQL transaction. Two competing
Publishers cannot both acquire the ID. Only that owner can submit later releases.

The proposal ID is the submission retry identity. If a response was lost, read
back status or resubmit that same proposal; successful submission retries return
the existing request even after proposal expiry. A transient error publishes no
partial listing. For changed or expired proposals, inspect again and deliberately
submit a new proposal. Review feedback is visible in My submissions and emailed.
For changes requested, fix hosted metadata, inspect it again, and submit a new
request referencing the previous request in the note. The prior review remains
in history. Rejected requests remain private and do not claim identity.

Authors can withdraw pending requests with `withdraw-request --id ID`, list
owned releases with `packages`, or change an exact release's visibility with
`withdraw-release --id PACKAGE_ID --version VERSION --confirm` and
`relist-release ... --confirm`. Administrator removal overrides author visibility
and preserves ownership and review history. It never removes installed copies.

Manager â†’ Installed Packages â†’ Install Package â†’ Browse Catalogue provides
public search and exact release details. Installing uses the Publisher-hosted
Manifest and archive through the existing checks, progress and installation-wide
replacement confirmation. Public HTTPS Manifest URLs remain usable when Catalogue is offline or a release
is no longer listed.
