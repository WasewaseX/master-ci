using System;

namespace Warehouse.Legacy
{
    public sealed class LegacyBridge
    {
        public void Call()
        {
#pragma warning disable 618
            Console.WriteLine("legacy");
#pragma warning restore 618
        }
    }
}
