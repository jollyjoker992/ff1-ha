---
name: ff1-playlists
description: Find art and build a playlist for a Feral File FF1 (the art computer on the wall) from public sources - an artist's works, a collection or series (Art Blocks, Feral File exhibitions, objkt and Tezos collections), a link to a work, or an artist's wallet address. Use when someone asks to show, play, display or put on an artist, a collection, an exhibition or a specific work, asks what there is by an artist, or wants something new on the art screen.
---

# Playlists for the FF1

`ff1-playlist` finds art in four public sources and turns it into something the FF1 can play. It asks no
key and no account, calls only those four sources, and never talks to the FF1 itself: it prints a `request`,
and the tool that controls the FF1 in this deployment sends it.

| Source | Knows | Asked by |
|---|---|---|
| `feed` | playlists already curated and signed by Feral File (its exhibitions), Art Blocks and objkt | a title |
| `artblocks` | every Art Blocks project and its mints | a project name or an artist name |
| `objkt` | Tezos collections and artists | a collection name, an artist's alias, a `KT1…` or `tz…` address |
| `resolver` | what a link names, and an artist's works across platforms (through Raster) | a link to a work or series, or an artist's wallet address |

```sh
ff1-playlist find "<artist, collection, link or address>"     # what the sources know, best first
ff1-playlist build "<the same>" [--limit 30]                  # the best match, built
ff1-playlist build --ref <vendor:kind:id> [--limit 30]        # one line of `find`, by its ref
```

## How to use it

1. **Know what was asked.** A name can be an artist or a collection; the tool tries both in every source and
   ranks them: a title that matches exactly first, a curated and signed feed playlist before a list built
   here, the collection's own platform before the rest.
2. **When the request is clear, `build` it directly** with the person's words. Its answer says what it chose
   (`title`, `from`, `works`): tell the person that in one line ("Fidenza, 8 works, Art Blocks' own playlist").
3. **When it could mean several things** (a common word, an artist with many series, two artists of one
   name), run `find`, show the top few (title, artist, source, number of works) and build the `ref` they pick.
   Don't guess between two different artists.
4. **Nothing found** is an answer: say so and name what was tried. Never invent a title, and never build from
   a URL of your own making; a link must come from the person.
5. Hand `request` to the FF1 tool unchanged. Do not edit a built playlist: it is signed, and an edit breaks
   the signature.

## Reading the answer

- `request` is `{"playlistUrl": …}` for a feed playlist (played as its curator signed it) or
  `{"dp1_call": …}` for a DP-1 playlist built here (signed as `curator` with this installation's key,
  `signed_by`). With `--out FILE` the request goes to that file instead.
- `works` is how many it holds; `--limit` is 1 to 100 (default 30). For an artist, the works come from every
  platform Raster knows when it knows more different pieces than the source that named the artist.
- `failed` names a source that did not answer. The result is still good; mention it only if nothing was found.
- Each work plays for a while and the next follows: about 10 minutes for live code, 5 for video, 2 for a
  still. The FF1's own settings (its content policy, set by its owner) may skip a work.

## What it does not do

It does not search by mood or subject, does not know what the household owns, and does not publish anything:
a playlist built here exists only in the request. `ff1-playlist key` shows the signing identity
(`did:key:…`); never print or copy the key file itself.
