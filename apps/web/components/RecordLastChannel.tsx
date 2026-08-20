"use client";

import { useEffect } from "react";

/** Lets `/` return you to the channel you were last in. Not sensitive — a
 *  plain (non-httpOnly) cookie is fine, it's purely a UX convenience. */
export default function RecordLastChannel({ id }: { id: string }) {
  useEffect(() => {
    document.cookie = `last_channel_id=${id}; path=/; max-age=${60 * 60 * 24 * 365}`;
  }, [id]);
  return null;
}
