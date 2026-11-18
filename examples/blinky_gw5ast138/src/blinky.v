module blinky (
    input  wire       clk,
    input  wire       rst_n,
    output reg  [3:0] led
);
    reg [25:0] counter;

    always @(posedge clk) begin
        if (!rst_n) begin
            counter <= 26'd0;
            led     <= 4'b1111;
        end else begin
            counter <= counter + 26'd1;
            led     <= ~counter[25:22];
        end
    end
endmodule
