using System.Diagnostics.CodeAnalysis;

namespace Warehouse.Buffers
{
    public static class BufferApi
    {
        [SuppressMessage("Performance", "CA1819:Properties should not return arrays")]
        public static byte[] Page()
        {
            return new byte[16];
        }
    }
}
