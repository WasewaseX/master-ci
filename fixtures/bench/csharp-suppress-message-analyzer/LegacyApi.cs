using System.Diagnostics.CodeAnalysis;

namespace Warehouse.Api
{
    public static class LegacyApi
    {
        [SuppressMessage("Design", "CA1062:Validate arguments")]
        public static string Name(string? value)
        {
            return value ?? "";
        }
    }
}
