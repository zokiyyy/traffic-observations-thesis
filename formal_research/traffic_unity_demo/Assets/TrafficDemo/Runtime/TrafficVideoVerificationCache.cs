using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace TrafficDemo
{
    // A cache entry is written only after the complete MP4 has matched its catalogue SHA256.
    // File identity is checked again on every use; changed files take the full hash path.
    internal static class TrafficVideoVerificationCache
    {
        private const string FileName = "verified_videos.json";
        private const string Schema = "verified_video_cache_v1";
        private static readonly Dictionary<string, TrafficVideoCacheEntry> entries =
            new Dictionary<string, TrafficVideoCacheEntry>(StringComparer.Ordinal);
        private static bool loaded;

        internal static bool TryGet(string path, string expectedSha256, out FileInfo info)
        {
            info = new FileInfo(path);
            if (!info.Exists) return false;
            Load();
            TrafficVideoCacheEntry entry;
            return entries.TryGetValue(path, out entry) &&
                string.Equals(entry.sha256, expectedSha256, StringComparison.OrdinalIgnoreCase) &&
                Matches(entry, info);
        }

        internal static bool SameFile(FileInfo before, FileInfo after)
        {
            return before.Exists && after.Exists && before.Length == after.Length &&
                before.LastWriteTimeUtc.Ticks == after.LastWriteTimeUtc.Ticks &&
                before.CreationTimeUtc.Ticks == after.CreationTimeUtc.Ticks;
        }

        internal static void Store(string path, string verifiedSha256, FileInfo info)
        {
            Load();
            entries[path] = new TrafficVideoCacheEntry {
                path = path, sha256 = verifiedSha256, length = info.Length,
                last_write_utc_ticks = info.LastWriteTimeUtc.Ticks,
                creation_utc_ticks = info.CreationTimeUtc.Ticks
            };
            try
            {
                string target = Path.Combine(Application.persistentDataPath, FileName);
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                var cache = new TrafficVideoCacheFile { schema_version = Schema, entries = Snapshot() };
                string temporary = target + ".tmp";
                File.WriteAllText(temporary, JsonUtility.ToJson(cache));
                if (File.Exists(target)) File.Delete(target);
                File.Move(temporary, target);
            }
            catch (Exception e)
            {
                // A read-only profile affects only future cache hits, never verification.
                Debug.LogWarning("Could not save video verification cache: " + e.Message);
            }
        }

        private static bool Matches(TrafficVideoCacheEntry entry, FileInfo info)
        {
            return entry.length == info.Length &&
                entry.last_write_utc_ticks == info.LastWriteTimeUtc.Ticks &&
                (entry.creation_utc_ticks == 0 || entry.creation_utc_ticks == info.CreationTimeUtc.Ticks);
        }

        private static void Load()
        {
            if (loaded) return;
            loaded = true;
            Read(Path.Combine(Application.streamingAssetsPath, "five_sources", FileName));
            Read(Path.Combine(Application.persistentDataPath, FileName));
        }

        private static void Read(string path)
        {
            try
            {
                if (!File.Exists(path)) return;
                var cache = JsonUtility.FromJson<TrafficVideoCacheFile>(File.ReadAllText(path));
                if (cache == null || cache.schema_version != Schema || cache.entries == null) return;
                foreach (var entry in cache.entries)
                    if (entry != null && !string.IsNullOrEmpty(entry.path) && !string.IsNullOrEmpty(entry.sha256))
                        entries[entry.path] = entry;
            }
            catch (Exception e)
            {
                Debug.LogWarning("Could not read video verification cache: " + e.Message);
            }
        }

        private static TrafficVideoCacheEntry[] Snapshot()
        {
            var result = new TrafficVideoCacheEntry[entries.Count];
            entries.Values.CopyTo(result, 0);
            return result;
        }
    }

    [Serializable] internal sealed class TrafficVideoCacheFile
    {
        public string schema_version;
        public TrafficVideoCacheEntry[] entries;
    }

    [Serializable] internal sealed class TrafficVideoCacheEntry
    {
        public string path, sha256;
        public long length, last_write_utc_ticks, creation_utc_ticks;
    }
}
