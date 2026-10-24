using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace TrafficDemo
{
    internal struct TrafficDetectionBox
    {
        internal ushort x1, y1, x2, y2;
        internal uint trackId;
        internal byte classId, zoneIndex, confidence;
    }

    // A compact, read-only rendering cache made from the saved full-length
    // tracks_primary records. It performs no inference and never changes states.
    internal sealed class TrafficDetectionOverlay
    {
        private readonly byte[] bytes;
        private readonly uint[] offsets;
        private readonly int recordsStart;
        internal int FrameCount { get { return offsets.Length - 1; } }

        private TrafficDetectionOverlay(byte[] bytes, uint[] offsets, int recordsStart)
        { this.bytes = bytes; this.offsets = offsets; this.recordsStart = recordsStart; }

        internal static TrafficDetectionOverlay Load(DemoSource source)
        {
            if (string.IsNullOrWhiteSpace(source.detection_boxes_path)) return null;
            string root = Path.GetFullPath(Application.streamingAssetsPath).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (Path.IsPathRooted(source.detection_boxes_path))
                throw new InvalidDataException("Detection box cache path must be relative to StreamingAssets.");
            string path = Path.GetFullPath(Path.Combine(root, source.detection_boxes_path));
            if (!path.StartsWith(root, StringComparison.Ordinal) || !File.Exists(path))
                throw new InvalidDataException("The saved detection box cache is missing or outside StreamingAssets.");
            byte[] bytes = File.ReadAllBytes(path);
            return FromBytes(source, bytes);
        }

        internal static TrafficDetectionOverlay FromBytes(DemoSource source, byte[] bytes)
        {
            if (source == null || bytes == null)
                throw new InvalidDataException("Detection box data is unavailable.");
            using (var sha = SHA256.Create())
            {
                string actual = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
                if (!string.Equals(actual, source.detection_boxes_sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("The saved detection box cache SHA256 does not match its source summary.");
            }
            if (!BitConverter.IsLittleEndian || bytes.Length < 56 ||
                Encoding.ASCII.GetString(bytes, 0, 8) != "TRBOX1\0\0")
                throw new InvalidDataException("Unsupported saved detection box cache format.");
            int width = BitConverter.ToInt32(bytes, 8), height = BitConverter.ToInt32(bytes, 12);
            int count = BitConverter.ToInt32(bytes, 16);
            if (width != source.video_width || height != source.video_height ||
                count != source.frame_count || count < 1 ||
                (long)52 + 4L * (count + 1) > bytes.Length)
                throw new InvalidDataException("Detection box cache does not match this video geometry or frame count.");
            byte[] videoHash = new byte[32];
            Buffer.BlockCopy(bytes, 20, videoHash, 0, 32);
            string cacheVideoHash = BitConverter.ToString(videoHash).Replace("-", "").ToLowerInvariant();
            if (!string.Equals(cacheVideoHash, source.video_sha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Detection boxes belong to a different video.");
            var offsets = new uint[count + 1];
            for (int i = 0; i < offsets.Length; i++) offsets[i] = BitConverter.ToUInt32(bytes, 52 + i * 4);
            int start = 52 + offsets.Length * 4;
            if (offsets[0] != 0 || offsets[count] != bytes.Length - start)
                throw new InvalidDataException("Detection box cache index is incomplete.");
            for (int i = 0; i < count; i++)
            {
                long a = start + offsets[i], b = start + offsets[i + 1];
                if (a < start || b > bytes.Length || b < a + 2 ||
                    b - a != 2 + 16L * BitConverter.ToUInt16(bytes, (int)a))
                    throw new InvalidDataException("Detection box cache frame index is invalid.");
            }
            return new TrafficDetectionOverlay(bytes, offsets, start);
        }

        internal void Collect(int frameIndex, List<TrafficDetectionBox> destination)
        {
            destination.Clear();
            if (frameIndex < 0 || frameIndex >= FrameCount) return;
            int position = recordsStart + (int)offsets[frameIndex];
            int count = BitConverter.ToUInt16(bytes, position);
            position += 2;
            for (int i = 0; i < count; i++, position += 16)
                destination.Add(new TrafficDetectionBox {
                    x1 = BitConverter.ToUInt16(bytes, position), y1 = BitConverter.ToUInt16(bytes, position + 2),
                    x2 = BitConverter.ToUInt16(bytes, position + 4), y2 = BitConverter.ToUInt16(bytes, position + 6),
                    trackId = BitConverter.ToUInt32(bytes, position + 8), classId = bytes[position + 12],
                    zoneIndex = bytes[position + 13], confidence = bytes[position + 14]
                });
        }

        internal static string ClassName(byte id)
        {
            switch (id) { case 0: return "car"; case 1: return "motorcycle"; case 2: return "bus"; case 3: return "truck"; }
            return "vehicle";
        }
    }
}
