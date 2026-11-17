using System.Threading.Tasks;

namespace Billing
{
    public class OrderService
    {
        private readonly IInvoiceGateway _gateway;

        public OrderService(IInvoiceGateway gateway)
        {
            _gateway = gateway;
        }

        public async void SaveOrderAsync(Order order)
        {
            var invoice = Invoice.From(order);
            await _gateway.SendAsync(invoice);
        }
    }
}
