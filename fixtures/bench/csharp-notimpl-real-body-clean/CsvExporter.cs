using System.Text;

namespace Warehouse.Exports
{
    public sealed class CsvExporter
    {
        public byte[] Export(string report)
        {
            var rows = report.Replace(';', ',');
            var body = "sku,qty" + rows;
            return Encoding.UTF8.GetBytes(body);
        }
    }
}
