#if UNITY_WEBGL && !UNITY_EDITOR
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using UnityEngine;
using UnityEngine.Networking;

namespace TrafficDemo
{
    public sealed partial class TrafficReplayController
    {
        private static string WebAssetUrl(string relative)
        {
            if (string.IsNullOrWhiteSpace(relative) || relative.StartsWith("/") ||
                relative.Contains("\\") || relative.Contains("..") || relative.Contains(":") ||
                relative.Contains("?") || relative.Contains("#"))
                throw new InvalidDataException("Invalid hosted asset path.");
            return Application.streamingAssetsPath.TrimEnd('/') + "/" + relative;
        }

        private static string WebVideoUrl(string relative, string remoteUrl)
        {
            if (string.IsNullOrWhiteSpace(relative) || !relative.StartsWith("media/", StringComparison.Ordinal) ||
                relative.Contains("\\") || relative.Contains("..") || relative.Contains(":") ||
                relative.Contains("?") || relative.Contains("#") ||
                !relative.EndsWith(".mp4", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Invalid hosted MP4 path.");
            if (!string.IsNullOrWhiteSpace(remoteUrl))
            {
                Uri remote;
                if (!Uri.TryCreate(remoteUrl, UriKind.Absolute, out remote) ||
                    remote.Scheme != Uri.UriSchemeHttps || !string.IsNullOrEmpty(remote.UserInfo) ||
                    !string.IsNullOrEmpty(remote.Fragment))
                    throw new InvalidDataException("Invalid hosted video URL.");
                bool b2 = remote.Host.EndsWith(".backblazeb2.com", StringComparison.OrdinalIgnoreCase) &&
                    remote.AbsolutePath.EndsWith("/" + relative, StringComparison.Ordinal) &&
                    string.IsNullOrEmpty(remote.Query);
                bool ccd = remote.Host.EndsWith(".client-api.unity3dusercontent.com", StringComparison.OrdinalIgnoreCase) &&
                    remote.AbsolutePath.Contains("/release_by_badge/latest/entry_by_path/content/") &&
                    remote.Query == "?path=" + Path.GetFileName(relative);
                if (!b2 && !ccd)
                    throw new InvalidDataException("Invalid hosted video URL.");
                return remote.AbsoluteUri;
            }
            var page = new Uri(Application.absoluteURL, UriKind.Absolute);
            if (page.Scheme != Uri.UriSchemeHttp && page.Scheme != Uri.UriSchemeHttps)
                throw new InvalidDataException("Open the dashboard from an HTTP(S) server.");
            return new Uri(page, relative).AbsoluteUri;
        }

        private static string Sha256(byte[] bytes)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private IEnumerator LoadWebData()
        {
            int generation = loadGeneration;
            catalog = null;
            recordingGroups.Clear();
            notice = "Loading the hosted recording catalog...";
            byte[] catalogBytes;
            using (var request = UnityWebRequest.Get(WebAssetUrl("five_sources/catalog.json")))
            {
                yield return request.SendWebRequest();
                if (generation != loadGeneration) yield break;
                if (request.result != UnityWebRequest.Result.Success)
                { Fail("Could not load the recording catalog: " + request.error); showSettings = true; yield break; }
                catalogBytes = request.downloadHandler.data;
            }

            TrafficDemoCatalog loaded;
            TrafficDemoCatalogEntry entry;
            try
            {
                loaded = JsonUtility.FromJson<TrafficDemoCatalog>(System.Text.Encoding.UTF8.GetString(catalogBytes));
                if (loaded == null || loaded.schema_version != "ten_source_catalog_v1" ||
                    loaded.entries == null || loaded.entries.Length != 10)
                    throw new InvalidDataException("The web catalog must contain the ten current recordings.");
                var ids = new HashSet<string>(StringComparer.Ordinal);
                foreach (var item in loaded.entries)
                {
                    if (item == null || string.IsNullOrWhiteSpace(item.id) ||
                        string.IsNullOrWhiteSpace(item.label) || !ids.Add(item.id))
                        throw new InvalidDataException("The web catalog contains an invalid or duplicated source.");
                    WebAssetUrl(item.data_path);
                    WebVideoUrl(item.video_path, item.video_url);
                }
                sourceIndex = Math.Max(0, Math.Min(sourceIndex, loaded.entries.Length - 1));
                entry = loaded.entries[sourceIndex];
            }
            catch (Exception ex)
            { Fail("Invalid web catalog: " + ex.Message); showSettings = true; yield break; }

            notice = "Loading the selected recording's saved analysis...";
            byte[] dataBytes;
            using (var request = UnityWebRequest.Get(WebAssetUrl(entry.data_path)))
            {
                yield return request.SendWebRequest();
                if (generation != loadGeneration) yield break;
                if (request.result != UnityWebRequest.Result.Success)
                { Fail("Could not load the selected analysis: " + request.error); showSettings = true; yield break; }
                dataBytes = request.downloadHandler.data;
            }

            TrafficDemoData loadedData;
            try
            {
                if (!string.Equals(Sha256(dataBytes), entry.data_sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("The analysis SHA256 does not match the web catalog.");
                loadedData = JsonUtility.FromJson<TrafficDemoData>(System.Text.Encoding.UTF8.GetString(dataBytes));
                if (loadedData == null) throw new InvalidDataException("The selected analysis is unreadable.");
                loadedData.Validate();
                if (loadedData.source.id != entry.id ||
                    !string.Equals(loadedData.source.video_sha256, entry.video_sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("The analysis source does not match the selected video entry.");
            }
            catch (Exception ex)
            { Fail("Invalid hosted analysis: " + ex.Message); showSettings = true; yield break; }

            TrafficDetectionOverlay loadedOverlay = null;
            if (!string.IsNullOrWhiteSpace(loadedData.source.detection_boxes_path))
            {
                notice = "Loading the selected recording's saved vehicle boxes...";
                byte[] boxes;
                using (var request = UnityWebRequest.Get(WebAssetUrl(loadedData.source.detection_boxes_path)))
                {
                    yield return request.SendWebRequest();
                    if (generation != loadGeneration) yield break;
                    if (request.result != UnityWebRequest.Result.Success)
                    { Fail("Could not load the vehicle boxes: " + request.error); showSettings = true; yield break; }
                    boxes = request.downloadHandler.data;
                }
                try { loadedOverlay = TrafficDetectionOverlay.FromBytes(loadedData.source, boxes); }
                catch (Exception ex)
                { Fail("Invalid vehicle boxes: " + ex.Message); showSettings = true; yield break; }
            }

            catalog = loaded;
            data = loadedData;
            detectionOverlay = loadedOverlay;
            RebuildRecordingGroups();
            pathText = WebVideoUrl(entry.video_path, entry.video_url);
            OpenVideo();
        }

        private void OpenWebVideo()
        {
            try
            {
                if (catalog == null || sourceIndex < 0 || sourceIndex >= catalog.entries.Length)
                    throw new InvalidDataException("No hosted recording is selected.");
                // Browser video playback streams the MP4. Its full-file SHA256 is recorded
                // in the catalog, but is not rehashed by the browser at load time.
                var entry = catalog.entries[sourceIndex];
                verifiedPath = WebVideoUrl(entry.video_path, entry.video_url);
                pathText = verifiedPath;
                PrepareVerifiedDecoder();
            }
            catch (Exception ex)
            { Fail("Hosted video unavailable: " + ex.Message); showSettings = true; }
        }
    }
}
#endif
