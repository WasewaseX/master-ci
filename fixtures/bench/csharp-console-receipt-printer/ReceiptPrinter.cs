using System;
using System.Globalization;

namespace Warehouse.Checkout
{
    public sealed class ReceiptPrinter
    {
        public string Total(decimal amount)
        {
            var line = amount.ToString("C", CultureInfo.CurrentCulture);
            Console.WriteLine("TOTAL " + line);
            return line;
        }
    }
}
