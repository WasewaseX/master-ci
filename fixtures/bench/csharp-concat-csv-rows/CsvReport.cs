using System.Collections.Generic;

namespace Warehouse.Reports
{
    public sealed class CsvReport
    {
        public string Render(IReadOnlyList<string> rows)
        {
            var csv = "sku,qty";
            foreach (var row in rows)
            {
                csv += ",";
            }
            return csv;
        }
    }
}
