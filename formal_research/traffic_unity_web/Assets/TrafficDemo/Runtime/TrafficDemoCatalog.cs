using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using UnityEngine;

namespace TrafficDemo
{
    [Serializable] public sealed class TrafficDemoCatalog
    {
        public string schema_version;
        public TrafficDemoCatalogEntry[] entries;
        [NonSerialized] string directory;

        public static TrafficDemoCatalog Load(string path)
        {
            if (!File.Exists(path)) throw new InvalidDataException("Source catalog is missing: " + path);
            var catalog = JsonUtility.FromJson<TrafficDemoCatalog>(File.ReadAllText(path));
            if (catalog == null || (catalog.schema_version != "five_source_catalog_v1" && catalog.schema_version != "ten_source_catalog_v1") ||
                catalog.entries == null || catalog.entries.Length == 0)
                throw new InvalidDataException("The source catalog has an unsupported schema or no sources.");
            catalog.directory = Path.GetDirectoryName(Path.GetDirectoryName(Path.GetFullPath(path)));
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (var entry in catalog.entries)
            {
                if (entry == null || string.IsNullOrWhiteSpace(entry.id) || !ids.Add(entry.id) || string.IsNullOrWhiteSpace(entry.label))
                    throw new InvalidDataException("Catalog source identifiers or labels are missing or duplicated.");
                catalog.ResolveDataPath(entry);
            }
            return catalog;
        }

        // This catalog lives in StreamingAssets/five_sources; entries are relative
        // to StreamingAssets, never to the process cwd.
        public string ResolveDataPath(TrafficDemoCatalogEntry entry)
        {
            if (entry == null || string.IsNullOrWhiteSpace(entry.data_path) || Path.IsPathRooted(entry.data_path))
                throw new InvalidDataException("Each catalog data path must be relative to StreamingAssets.");
            string path = Path.GetFullPath(Path.Combine(directory, entry.data_path));
            if (!path.StartsWith(directory + Path.DirectorySeparatorChar, StringComparison.Ordinal))
                throw new InvalidDataException("A catalog data path escapes StreamingAssets.");
            return path;
        }

        public TrafficDemoData LoadData(TrafficDemoCatalogEntry entry)
        {
            string path = ResolveDataPath(entry);
            if (!File.Exists(path)) throw new InvalidDataException("The selected source summary is missing: " + path);
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(path))
            {
                string hash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                if (!string.Equals(hash, entry.data_sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("The selected source summary does not match its catalog SHA256.");
            }
            var data = TrafficDemoData.Load(path);
            if (data.source.id != entry.id || data.source.video_sha256 != entry.video_sha256)
                throw new InvalidDataException("The selected source summary does not match its catalog video identity.");
            return data;
        }
    }

    [Serializable] public sealed class TrafficDemoCatalogEntry
    {
        public string id, label, data_path, data_sha256;
        public string source_state_path, source_state_sha256, source_rules_path, source_rules_sha256;
        public string video_path, video_url, video_sha256;
    }
}
