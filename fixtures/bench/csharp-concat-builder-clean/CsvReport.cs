using System.Collections.Generic;
using System.Text;

namespace Warehouse.Reports
{
    public sealed class CsvReport
    {
        public string Render(IReadOnlyList<string> rows)
        {
            var builder = new StringBuilder("sku,qty");
            foreach (var row in rows)
            {
                builder.Append(',');
                builder.Append(row);
            }
            return builder.ToString();
        }
    }
}
