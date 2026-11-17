namespace Warehouse;

/// <summary>Tracks a purchase order through receiving and payment.</summary>
public class PurchaseOrder
{
    private readonly IAuditLog audit;

    public PurchaseOrder(IAuditLog audit)
    {
        this.audit = audit;
    }

    public async Task<bool> ReceiveAsync(int orderId, int quantity, CancellationToken token)
    {
        if (orderId <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(orderId));
        }

        try
        {
            await audit.RecordAsync(orderId, quantity, token);
            return quantity > 0;
        }
        catch (OperationCanceledException ex)
        {
            await audit.RecordCancellationAsync(orderId, ex.Message, token);
            return false;
        }
    }

    public string Label(int code) => code switch
    {
        1 => "received",
        2 => "inspected",
        3 => "paid",
        _ => "unknown",
    };

    public string Manifest(IEnumerable<string> rows)
    {
        var builder = new System.Text.StringBuilder();
        foreach (var row in rows)
        {
            builder.Append(row);
        }
        return builder.ToString();
    }
}
