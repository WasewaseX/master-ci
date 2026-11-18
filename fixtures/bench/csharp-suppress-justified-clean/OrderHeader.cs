using System.Diagnostics.CodeAnalysis;

namespace Warehouse.Models
{
    public sealed class OrderHeader
    {
#pragma warning disable CS8618 // EF core materializes the backing fields
        public string Customer { get; set; }
#pragma warning restore CS8618

        [SuppressMessage("Design", "CA1062:Validate arguments", Justification = "callers are internal")]
        public string Name() => Customer ?? "";
    }
}
