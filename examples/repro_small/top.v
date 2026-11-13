module top(input clk, input [3:0] a, output reg [1:0] q = 2'b00);
    always @(posedge clk) begin
        q[0] <= (a[0] ^ a[1]) & (a[2] ^ a[3]);
        q[1] <= (a[0] & a[1]) ^ (a[2] | a[3]);
    end
endmodule
